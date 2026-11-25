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
    from hashlib import sha256
    from .between_motion import anchor_of,component_modes,solve_serial_weights
    from .manual_regions import aligned_partition,route_box_spans,usable_partition
    from .reconstruction_edits import apply_angles
    attached=defaultdict(list);seeded={};result=[]
    for row in r.settings.reconstruction_added:
        layer,index,x,y,identity,*scope=row
        candidates=[e for e in entries if (e['layer'],e['index'])==(layer,index) and e['plan'] is not None]
        if scope and scope!=['seed']:candidates=[e for e in candidates if e['plan'].identity==scope[0]]
        if candidates:
            best=min(candidates,key=lambda e:e['plan'].route.distance(shapely.Point(x,y)))
            if scope==['seed']:seeded[id(best)]=(x,y,identity)
            else:attached[id(best)].append((x,y,identity,scope))
    box_excluded=set(r.settings.reconstruction_box_excluded)
    ignored_set=set(r.settings.reconstruction_ignored)
    deleted_set=set(r.settings.reconstruction_deleted)
    boxes=r.ordered_source_boxes
    by_source=defaultdict(list)
    for entry in entries:
        ref=(entry['layer'],entry['index'])
        if ref in r.box_refs and entry['plan'] is not None:
            by_source[ref].append(entry)
    # A two-ended trace can have two equivalent paths with opposite directions.
    # A box crossing their ownership seam still defines just one physical cut.
    mirrored={}
    for ref,peers in by_source.items():
        if len(peers)!=2 or not all(e.get('paired_vias') for e in peers):continue
        a,b=(e['plan'] for e in peers)
        if abs(a.length-b.length)<=r.tol and a.route.equals_exact(b.route.reverse(),r.tol):
            mirrored[ref]=peers
    box_parts={}
    crossed_routes={}

    def parts_for(entry,plan,is_free,polygon):
        seam=entry.get('ownership_seam')
        spans=route_box_spans(plan,polygon)
        for span in spans or (None,):
            part=usable_partition(plan,polygon,span) if is_free else aligned_partition(plan,polygon,span)
            if is_free and (entry.get('graph_branch') or (seam is not None and not seam.is_empty)):
                if part is None or part.get('box_invalid') or part.get('box_complex'):
                    fitted=aligned_partition(plan,polygon,span)
                    if fitted is not None:
                        # Fit only this visited branch when the free rectangle
                        # cannot form two faces at a fork or an ownership seam.
                        part=dict(fitted,box_refitted=True)
            if part is not None:yield part

    def assigned_boxes(entry,plan,ref):
        peers=mirrored.get(ref)
        for box_idx,(is_free,polygon) in enumerate(boxes.get(ref,())):
            if not peers:
                if not r._box_applies(plan,polygon):continue
                if entry.get('graph_branch'):
                    cache_key=(ref,box_idx)
                    if cache_key not in crossed_routes:
                        crossed_routes[cache_key]={id(peer) for peer in by_source[ref]
                                                   if peer['plan'].route.intersection(polygon).length>r.tol}
                    crossed=crossed_routes[cache_key]
                    if crossed and id(entry) not in crossed:continue
                for part in parts_for(entry,plan,is_free,polygon):yield is_free,polygon,part
                continue
            cache_key=(ref,box_idx)
            if cache_key not in box_parts:
                candidates=[]
                for peer in peers:
                    route=peer['plan']
                    parts=tuple(parts_for(peer,route,is_free,polygon))
                    if not parts:continue
                    domain=getattr(route,'ownership_domain',route.source)
                    area=sum(domain.intersection(part['A']).area for part in parts)
                    candidates.append(((sum(not p.get('box_invalid',False) for p in parts),area,key(route)),peer,parts))
                box_parts[cache_key]=max(candidates,key=lambda c:c[0])[1:] if candidates else (None,())
            peer,parts=box_parts[cache_key]
            if peer is entry:
                for part in parts:yield is_free,polygon,part

    def _clone_child(plan,identity,a,b):
        child=copy.copy(plan);child.cache={};child.pose_memo=OrderedDict();child.memo_bytes=0
        child.identity=identity;child.nominal_first=float(a);child.nominal_second=float(b)
        return child

    def _finish_serial(rows,entry):
        usable=[e for e in rows if e.get('serial_part') is not None]
        invalid=[e for e in rows if e.get('serial_part') is None]
        usable.sort(key=lambda e:e['serial_part']['a'])
        merged=[]
        for e in usable:
            if merged and e['serial_part']['a']<=merged[-1]['serial_part']['b']+r.tol:
                lead=merged[-1];a=lead['serial_part']['a'];b=max(lead['serial_part']['b'],e['serial_part']['b'])
                joined=lead['plan'].partition(a,b)
                if joined is not None:
                    joined=apply_angles(r.settings,lead['plan'],joined) or joined
                    lead['serial_part']=joined;lead['merged_part']=joined
                    lead['plan'].merged_keys=(*getattr(lead['plan'],'merged_keys',()),key(e['plan']))
                    continue
            merged.append(e)
        usable=merged
        if len(usable)<=1:
            out=invalid
            for e in usable:
                part=e.pop('serial_part',None)
                if part is not None:e['merged_part']=part
                out.append(e)
            return out
        # Compute intermediate rigid copper blocks and resolve their motion weights
        # from per-segment overrides or the global reconstruction_between_motion setting.
        ref=(entry['layer'],entry['index'])
        occupied=shapely.Polygon();blocks=[]
        fixed=usable[-1]['serial_part']['B']
        for e in usable:
            part=e['serial_part']
            block=part['C'].difference(occupied)
            blocks.append(block)
            occupied=shapely.union_all([occupied,part['A'],part['C']])
        between_modes=[];between_meta=[]
        modes=component_modes(r.settings,ref,dict(enumerate(blocks[1:])),r.tol)
        for i,block in enumerate(blocks[1:]):
            mode,custom=modes[i]
            between_modes.append(mode);between_meta.append((block,mode,custom))
        weights=solve_serial_weights(between_modes)
        for j,e in enumerate(usable):
            e.update(serial_left=weights[j],serial_right=weights[j+1],
                     serial_block=blocks[j],serial_fixed=fixed)
            if j>0:
                blk,mode,custom=between_meta[j-1]
                e['serial_between']=(blk,mode,custom,anchor_of(blk) if not blk.is_empty else None)
        return invalid+usable

    for entry in entries:
        original_entry=entry
        plan=entry['plan']
        ref=(entry['layer'],entry['index'])
        extras=attached.get(id(entry),());seed=seeded.get(id(entry))
        if seed and plan is not None:
            x,y,identity=seed;child=copy.copy(plan);child.cache={};child.pose_memo=OrderedDict();child.memo_bytes=0
            child.identity=plan.identity+'~'+identity
            a,b=plan.nominal_first,plan.nominal_second;position=float(plan.route.project(shapely.Point(x,y)))
            changed,_=child.edit(a,b,0,position-child.tolerance*16) if position<a else child.edit(a,b,1,position+child.tolerance*16) if position>b else (child.partition(a,b),False)
            if changed is not None:child.nominal_first,child.nominal_second=changed['a'],changed['b']
            entry=dict(entry,plan=child,seeded=True);plan=child

        applicable_boxes=tuple(assigned_boxes(original_entry,plan,ref)) if plan is not None and ref in r.box_refs and ref not in box_excluded else ()

        if plan is None:
            result.append(entry);continue

        # Step 1: Collect existing zones on this plan (unless ref was ignored before boxes).
        rows=[]
        had_only_boxes=(ref in ignored_set and bool(applicable_boxes))
        if not had_only_boxes:
            if not extras:
                if key(plan) not in deleted_set:
                    part,manual=r._interval_without_boxes(entry)
                    if part is not None:
                        local=dict(entry,serial_part=part,merged_part=part,serial_explicit=manual)
                        rows.append(local)
                    elif not applicable_boxes:
                        rows.append(entry)
            else:
                base,_=r._interval_without_boxes(entry)
                if base is None:
                    rows.append(entry)
                else:
                    a,b=(extras[0][3][1:] if extras[0][3] else (plan.nominal_first,plan.nominal_second))
                    marks=sorted((float(plan.route.project(shapely.Point(x,y))),identity) for x,y,identity,scope in extras)
                    spacing=max(plan.tolerance*32,min(plan.guard,(b-a)/(len(marks)+1)*.05))
                    boundaries=[a]
                    for j,(s,_) in enumerate(marks):
                        boundaries.append(float(np.clip(s,boundaries[-1]+spacing*4,b-(len(marks)-j)*spacing*4)))
                    boundaries.append(b)
                    identities=[None,*[identity for _,identity in marks]]
                    for j,identity in enumerate(identities):
                        cid=plan.identity if identity is None else plan.identity.split('~')[0]+'~'+identity
                        child=_clone_child(plan,cid,boundaries[j]+(spacing if j else 0),boundaries[j+1]-(spacing if j<len(identities)-1 else 0))
                        local=dict(entry,plan=child,serial_base=plan.identity,serial_explicit=True,boxed=identity is not None)
                        if key(child) in deleted_set:continue
                        part,_=r._interval_without_boxes(local)
                        if part is None:
                            local.update(plan=None,orphan=False,warning='新增动区截面无法分离连接端；保留原图并标记')
                        else:
                            local['serial_part']=part;local['merged_part']=part
                        rows.append(local)

        # Step 2: Apply boxed zones ADDITIVELY (never deleting non-overlapping existing zones;
        # overlapping boxes expand the existing zone's range).
        for box_idx,(is_free,bp,bpart) in enumerate(applicable_boxes):
            ba,bb=float(bpart['a']),float(bpart['b'])
            overlapping=[idx for idx,row in enumerate(rows)
                         if row.get('serial_part') is not None
                         and (row['serial_part']['a']<=bb+r.tol and ba<=row['serial_part']['b']+r.tol)]
            # A user's first box takes over an overlapping automatic section.
            # Otherwise the old automatic corridor absorbs every box put on it.
            automatic=[i for i in overlapping if not rows[i].get('serial_explicit')]
            if automatic:
                rows=[row for i,row in enumerate(rows) if i not in automatic]
                overlapping=[idx for idx,row in enumerate(rows) if row.get('serial_part') is not None
                             and row['serial_part']['a']<=bb+r.tol and ba<=row['serial_part']['b']+r.tol]
            if overlapping:
                # Expand existing overlapping zone(s) to cover union with this box.
                lead=rows[overlapping[0]]
                new_a=min(ba,*(rows[i]['serial_part']['a'] for i in overlapping))
                new_b=max(bb,*(rows[i]['serial_part']['b'] for i in overlapping))
                if is_free and not bpart.get('box_refitted'):
                    combined=shapely.union_all([*(rows[i]['serial_part']['A'] for i in overlapping),bp])
                    expanded=usable_partition(lead['plan'],combined)
                    if expanded is None:
                        angles=(bpart['angles'][0] if ba<=rows[overlapping[0]]['serial_part']['a'] else lead['serial_part'].get('angles',(0.,0.))[0],
                                bpart['angles'][1] if bb>=rows[overlapping[-1]]['serial_part']['b'] else lead['serial_part'].get('angles',(0.,0.))[1])
                        expanded=lead['plan'].partition(new_a,new_b,angles) or lead['plan'].partition(new_a,new_b)
                else:
                    expanded=lead['plan'].partition(new_a,new_b)
                    if expanded is not None:
                        expanded=apply_angles(r.settings,lead['plan'],expanded) or expanded
                if expanded is not None:
                    lead['serial_part']=expanded;lead['merged_part']=expanded
                    lead['boxed']=True
                    for i in reversed(overlapping[1:]):
                        lead['plan'].merged_keys=(*getattr(lead['plan'],'merged_keys',()),key(rows[i]['plan']))
                        rows.pop(i)
            else:
                # Disjoint box: add as an independent new movable zone on this plan!
                tag=sha256(repr((plan.identity,round(ba,4),round(bb,4),int(is_free),box_idx)).encode()).hexdigest()[:10]
                cid=plan.identity if not rows and key(plan) not in deleted_set else plan.identity.split('~')[0]+'~box'+tag
                child=_clone_child(plan,cid,ba,bb)
                if key(child) in deleted_set:continue
                local=dict(entry,plan=child,serial_base=plan.identity,serial_explicit=True,boxed=True)
                # Respect any subsequent drag/rotation edit saved on this boxed zone's identity.
                saved_part,saved_manual=r._interval_without_boxes(local)
                use_part=saved_part if (saved_manual and saved_part is not None) else bpart
                local['serial_part']=use_part;local['merged_part']=use_part
                rows.append(local)

        if not rows:continue
        if len(rows)==1 and not applicable_boxes and not extras:
            row=rows[0];row.pop('serial_part',None)
            result.append(row)
        else:
            result.extend(_finish_serial(rows,entry))
    return result


def serial_render(r,entry,part,center,pose):
    from .reconstruction import transform
    from .reconstruction_route import warp
    left,right=entry['serial_left'],entry['serial_right']
    adjusted=dict(part,C=entry['serial_block'],B=entry['serial_fixed'])
    if abs(left-right)<=1e-12 or not np.any(pose):
        active=transform(part['A'],center,pose*right)
        regions=dict(A=active,C=transform(entry['serial_block'],center,pose*left),B=entry['serial_fixed'])
        return adjusted,regions,None
    theta=right*pose[2];co,si=np.cos(theta),np.sin(theta)
    translation=np.array([[co,si],[-si,co]])@((left-right)*pose[:2])
    relative=np.r_[translation,(left-right)*pose[2]]
    geos,error=warp(part,entry['plan'],center,relative,r.settings.reconstruction_style)
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
    from .resources import cancel_check,serial_scope
    cancel_check()
    # Only this source's nominal paths are needed during a pointer move. Reuse
    # completed ownership/enclosure classification; do not rebuild all targets.
    base=getattr(original,'_base_plans',None)
    if base is not None:
        local_entries=[]
        for q in base:
            if (q['layer'],q['index'])!=ref:continue
            entry=dict(q)
            if entry.get('plan') is not None:
                plan=copy.copy(entry['plan'])
                # Plan caches are keyed by immutable (mode, style, part_key, pose);
                # reusing a shallow copy avoids recomputing unchanged zones on a
                # multi-zone polygon during interactive editing.
                plan.cache=dict(plan.cache)
                plan.pose_memo=OrderedDict();plan.memo_bytes=0
                entry['plan']=plan
            local_entries.append(entry)
        pool={'local':local_entries}
    else:
        pool=clone_plan_pool(original.plan_pool)
    same_rules=(settings.drc_checks,settings.drc_tolerance_um,settings.drc_min_angle_deg)==(original.settings.drc_checks,original.settings.drc_tolerance_um,original.settings.drc_min_angle_deg)
    rebuilt=Reconstructor(original.model,original.p,settings,plan_pool=pool,geometry_pool=getattr(original,'geometry_pool',None),reference=original if same_rules else None)
    rebuilt.vias_by_target={ref:original.vias_by_target[ref]} if ref in original.vias_by_target else {}
    rebuilt._envelopes=getattr(original,'_envelopes',None)
    if base is not None:
        rebuilt.auto_following=set(original.auto_following);rebuilt.auto_ignored=dict(original.auto_ignored);rebuilt.safe_vias=dict(original.safe_vias)
        if ref in settings.reconstruction_ignored and ref not in rebuilt.box_refs:
            from .ignored_motion import entries as ignored_entries
            rebuilt._base_plans=[];rebuilt._plans=ignored_entries(rebuilt)
        elif pool['local'] and (not draft or ref not in settings.reconstruction_ignored or ref in rebuilt.box_refs):
            rebuilt._base_plans=list(pool['local']);rebuilt._plans=expand(rebuilt,pool['local'])
    try:
        with serial_scope():
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
        between=tuple(b for b in data.get('between_blocks',()) if b['ref']!=ref)+tuple(local.get('between_blocks',()))
    finally:
        rebuilt.close()
    return dict(old,_reconstruction_changed_refs=tuple(sorted({ref,*repaired})),reconstruction=dict(data,configuration=settings.reconstruction_key(),pieces=rows,replacements=changed,between_blocks=between,violations=[],constraints_complete=False,repair_refs=repaired,hard_failures=blocked,electrically_valid=not bool(blocked)))
