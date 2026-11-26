"""Validated nominal-source edits shared across styles and preview poses."""
import math
from dataclasses import replace


def key(plan):return (*plan.key,plan.identity)


def retire_pad_intervals(settings,solver,pieces=()):
    """Keep the far end of each existing point-mode zone when closing the mode."""
    if settings.reconstruction_allow_moving_pad or solver is None:
        return settings
    rows={row[:5]:row for row in settings.reconstruction_intervals}
    for piece in pieces:
        for member in piece.get('graph_members',(piece,)):
            plan=member.get('route_plan');a,b=member['interval']
            if plan is not None and a==0 and member.get('strict_box') is None:
                identity=key(plan)
                rows.setdefault(identity,(*identity,a,b))
    if not any(row[5]==0 for row in rows.values()):return settings
    plans={key(plan):plan for entry in getattr(solver,'_plans',()) or ()
           if (plan:=entry['plan']) is not None}
    migrated=[]
    for row in rows.values():
        plan=plans.get(row[:5]) if row[5]==0 else None
        part=plan.moving_line_partition(row[6]) if plan is not None else None
        migrated.append((*row[:5],part['a'],part['b']) if part is not None else row)
    return replace(settings,reconstruction_intervals=tuple(migrated))


def blade_angles(settings,plan,a,b,defaults=(0.,0.)):
    angles=list(defaults);manual=[False,False]
    for row in settings.reconstruction_angles:
        if row[:5]!=key(plan):continue
        side,absolute=row[5:];v=plan.tangent(a if side==0 else b)
        # A blade is an unoriented line: angles differing by pi are identical.
        relative=(absolute-math.atan2(v[1],v[0])-math.pi/2+math.pi/2)%math.pi-math.pi/2
        angles[side]=relative;manual[side]=True
    return tuple(angles),tuple(manual)


def apply_angles(settings,plan,part):
    if part is None:return None
    plan.cut_mode='manual'
    angles,manual=blade_angles(settings,plan,part['a'],part['b'],part.get('angles',(0.,0.)))
    if angles==tuple(part.get('angles',(0.,0.))):return dict(part,manual_angles=manual)
    changed=plan.partition(part['a'],part['b'],angles)
    if changed is not None:changed=dict(changed,manual_angles=manual)
    return changed


def rotate(settings,piece,side,delta=None):
    plan=piece['route_plan'];identity=key(plan)
    rows=[r for r in settings.reconstruction_angles if (r[:5],r[5])!=(identity,side)]
    if delta is not None:
        s=piece['interval'][side];v=plan.tangent(s)
        previous=next((row[6] for row in settings.reconstruction_angles if (row[:5],row[5])==(identity,side)),None)
        angle=(previous if previous is not None else math.atan2(v[1],v[0])+math.pi/2+piece.get('boundary_angles',(0.,0.))[side])+delta
        rows.append((*identity,side,angle))
    forced=set(settings.reconstruction_forced);forced.add((plan.layer,plan.index))
    return replace(settings,reconstruction_angles=tuple(rows),reconstruction_forced=tuple(sorted(forced)))


def project_click(piece,point):
    """Map a displayed transition back to its common nominal arclength."""
    import shapely
    a,b=piece['interval'];near=piece.get('moved_cut');far=piece.get('extra_cut')
    if near is None or far is None:return float(piece['route_plan'].route.project(shapely.Point(point)))
    guide=shapely.LineString([near.centroid.coords[0],far.centroid.coords[0]])
    region=piece['regions']['A']
    if not region.is_empty and not region.covers(guide):
        from .reconstruction_route import trace
        interior=trace(region,near.centroid.coords[0],far.centroid.coords[0],max(1.,min(near.length,far.length)/2))
        if interior is not None:guide=interior
    if guide.length<=piece['route_plan'].tolerance:return (a+b)/2
    fraction=guide.project(shapely.Point(point))/guide.length
    return a+(b-a)*fraction


def change_zone(window,action,piece=None,ref=None,point=None,side=None,refs=()):
    if getattr(window,"remote_locked",False):return
    """Apply one source-bound edit; all poses share the resulting settings key."""
    import uuid
    cfg=window.view_settings
    if piece is not None:ref=(piece['layer'],piece['index'])
    if ref is None and action!='delete_all':return
    overlay=getattr(window,'reconstruction_overlay',None)
    displayed=(overlay.data() if overlay else (window.current_frame or {}).get('reconstruction')) or {}
    ignored=set(cfg.reconstruction_ignored);forced=set(cfg.reconstruction_forced)
    if action=='rotate_reset':
        updated=rotate(cfg,piece,side,None)
    elif action=='delete_all':
        refs={tuple(ref) for ref in refs}
        if not refs:return
        fields=('reconstruction_intervals','reconstruction_angles','reconstruction_deleted','reconstruction_added',
                'reconstruction_cuts','reconstruction_extra_cuts','reconstruction_second_cuts','reconstruction_dropped_cuts','reconstruction_between_overrides','reconstruction_endpoint_overrides')
        edits={name:tuple(row for row in getattr(cfg,name) if row[:2] not in refs) for name in fields}
        updated=replace(cfg,**edits,reconstruction_ignored=tuple(sorted(ignored|refs)),
                        reconstruction_forced=tuple(sorted(forced-refs)),
                        reconstruction_following=tuple(sorted(set(cfg.reconstruction_following)-refs)),
                        reconstruction_box_excluded=tuple(sorted(set(cfg.reconstruction_box_excluded)|refs)))
    elif action=='delete':
        plan=piece.get('route_plan')
        if plan is None:return
        deleted=set(cfg.reconstruction_deleted);deleted.update(key(q['route_plan']) for q in piece.get('graph_members',(piece,)) if q.get('route_plan') is not None)
        present=[q for q in displayed.get('pieces',()) if (q['layer'],q['index'])==ref and q.get('route_plan') is not None]
        box_excluded=set(cfg.reconstruction_box_excluded)
        if all(key(member['route_plan']) in deleted for q in present for member in q.get('graph_members',(q,))):
            ignored.add(ref)
            if any(row[0]==ref[0] for row in (*cfg.reconstruction_boxes,*cfg.reconstruction_angle_boxes)):
                box_excluded.add(ref)
        updated=replace(cfg,reconstruction_deleted=tuple(sorted(deleted)),reconstruction_ignored=tuple(sorted(ignored)),
                        reconstruction_box_excluded=tuple(sorted(box_excluded)))
    elif action=='add':
        ignored.discard(ref);forced.add(ref)
        current=[q for q in displayed.get('pieces',()) if (q['layer'],q['index'])==ref and q.get('route_plan') is not None]
        current=[member for q in current for member in q.get('graph_members',(q,))]
        added=cfg.reconstruction_added
        # Restoring an empty/ignored shape creates its normal automatic zones.
        # Additional clicks split the nearest existing route into serial zones.
        deleted=cfg.reconstruction_deleted
        intervals=list(cfg.reconstruction_intervals)
        if current:
            import shapely
            from .selection import inverse_geometry
            from .moving_pair import boundary_pose
            probe=shapely.Point(point);frame=window.current_frame
            def nominal_at(q):
                if q['bridge'].covers(probe):
                    return q['route_plan'].route.interpolate(project_click(q,point))
                side=0 if q['regions']['C'].covers(probe) else 1
                center,state=boundary_pose(q,frame,side)
                return inverse_geometry(probe,center,state) if state.any() else probe
            nominal_sites=[(q,nominal_at(q)) for q in current]
            target,nominal=min(nominal_sites,key=lambda item:(
                item[0]['route_plan'].route.distance(item[1]),item[0]['bridge'].distance(probe)))
            solver=getattr(getattr(window,'engine',None),'reconstructor',None)
            base_plans=[e['plan'] for e in (getattr(solver,'_base_plans',None) or ())
                        if (e['layer'],e['index'])==ref and e.get('plan') is not None]
            current_bases={q['route_plan'].identity.split('~')[0] for q in current}
            unused_plan=min((p for p in base_plans if p.identity.split('~')[0] not in current_bases),
                            key=lambda p:p.route.distance(nominal),default=None)
            use_unused=(unused_plan is not None and
                        unused_plan.route.distance(nominal)+unused_plan.tolerance<target['route_plan'].route.distance(nominal))
            plan=unused_plan if use_unused else target['route_plan']
            base_id=plan.identity.split('~')[0]
            a,b=(0.,plan.length) if use_unused else target['interval']
            if not plan.source.covers(nominal):
                nominal=shapely.shortest_line(plan.source,nominal).interpolate(0)
            position=float(plan.route.project(nominal))
            guard=max(plan.tolerance*32,plan.guard)
            half=max(guard*2,min((b-a)*.2,30.));chosen=None
            for scale in (1,2,4,8,16,32):
                left=max(0.,position-half*scale);right=min(plan.length,position+half*scale)
                part=plan.partition(left,right)
                if part is not None and part['A'].covers(nominal):chosen=(part['a'],part['b']);break
            if chosen is None:
                window.statusBar().showMessage('该点击位置不能形成保持连接的动区，未在其他位置替代添加');return
            left,right=chosen;identity=uuid.uuid4().hex[:12];new_id=base_id+'~'+identity
            saved={tuple(r[:5]):r for r in intervals}
            automatic=([] if use_unused else [q for q in current if q.get('graph_branch') and not q['manual']
                       and q['die']==target['die'] and q['nominal_regions']['A'].covers(nominal)])
            scoped=[q for q in current if q['route_plan'].identity.split('~')[0]==base_id]
            outer_a=min([left,*[q['interval'][0] for q in scoped]]);outer_b=max([right,*[q['interval'][1] for q in scoped]])
            if any(q is target for q in automatic):
                # All automatic branch paths using this same trunk must take the
                # local interval. An unedited sibling otherwise grows it back.
                ends=[plan.route.interpolate(s) for s in (left,right)]
                for q in automatic:
                    route=q['route_plan']
                    lo,hi=sorted(float(route.route.project(end)) for end in ends)
                    part=route.partition(lo,hi)
                    if part is not None:
                        identity_key=key(route);saved[identity_key]=(*identity_key,part['a'],part['b'])
            elif use_unused or position<a-guard or position>b+guard:
                for q in scoped:
                    identity_key=key(q['route_plan']);lo,hi=q['interval']
                    saved.setdefault(identity_key,(*identity_key,lo,hi))
                saved[(*plan.key,new_id)]=(*plan.key,new_id,left,right)
                added=(*added,(*ref,float(plan.route.interpolate(position).x),float(plan.route.interpolate(position).y),identity,base_id,left,right,'local'))
            else:
                for q in scoped:
                    identity_key=key(q['route_plan']);lo,hi=q['interval']
                    if q is target:
                        if position>=(a+b)/2:hi=min(hi,left-guard)
                        else:lo=max(lo,right+guard)
                        if hi<=lo:
                            deleted=tuple(set(deleted)|{identity_key});continue
                    saved[identity_key]=(*identity_key,lo,hi)
                saved[(*plan.key,new_id)]=(*plan.key,new_id,left,right)
                added=(*added,(*ref,float(plan.route.interpolate(position).x),float(plan.route.interpolate(position).y),identity,base_id,outer_a,outer_b))
            intervals=list(saved.values())
        else:
            import shapely
            from .selection import inverse_geometry
            for q in displayed.get('pieces',()):
                if (q['layer'],q['index'])==ref and q.get('ignored') and q['regions']['C'].covers(shapely.Point(point)):
                    point=inverse_geometry(shapely.Point(point),window.current_frame['rotation_centers'][q['die']],window.current_frame['states'][q['die']]).coords[0];break
            added=tuple(row for row in added if row[:2]!=ref)+((*ref,float(point[0]),float(point[1]),uuid.uuid4().hex[:12],'seed'),)
        updated=replace(cfg,reconstruction_added=added,reconstruction_deleted=deleted,reconstruction_intervals=tuple(intervals),
                        reconstruction_ignored=tuple(sorted(ignored)),reconstruction_forced=tuple(sorted(forced)))
    else:return
    if updated==cfg:return
    from .edit_history import record
    record(window)
    window.view_settings=updated;window.invalidate_reconstruction_edit();window.request_preview(quiet=True)


def normalize_edits(values):
    result={name:[] for name in ('reconstruction_forced','reconstruction_angles','reconstruction_deleted','reconstruction_added')}
    for name in result:
        for raw in values.get(name,()) or ():
            try:
                layer,index,*tail=raw;layer=str(layer);index=int(index)
                if index<0:continue
                if name=='reconstruction_forced':row=(layer,index)
                elif name=='reconstruction_added':
                    x,y,identity,*scope=tail;x,y=float(x),float(y)
                    if not all(map(math.isfinite,(x,y))):continue
                    row=(layer,index,x,y,str(identity))
                    if scope==['seed']:row+=('seed',)
                    elif len(scope)==3:
                        base,a,b=scope;a,b=float(a),float(b)
                        if not all(map(math.isfinite,(a,b))) or not 0<=a<b:continue
                        row+=(str(base),a,b)
                else:
                    owner,refs,identity,*rest=tail
                    refs=tuple(sorted((str(k),int(i)) for k,i in refs))
                    row=(layer,index,int(owner),refs,str(identity))
                    if name=='reconstruction_angles':
                        side,angle=rest;angle=float(angle)
                        if int(side) not in (0,1) or not math.isfinite(angle):continue
                        row+=int(side),angle
                if row not in result[name]:result[name].append(row)
            except (ValueError,TypeError,IndexError):continue
    return {k:tuple(v) for k,v in result.items()}


def commit_merges(settings,frame,ref=None,*,refs=None):
    """Absorbed serial zones must not reappear on later edits or Job reload."""
    rows={row[:5]:row for row in settings.reconstruction_intervals};deleted=set(settings.reconstruction_deleted)
    angles={(row[:5],row[5]):row for row in settings.reconstruction_angles}
    for q in (frame or {}).get('reconstruction',{}).get('pieces',()):
        source=(q['layer'],q['index'])
        if (ref is not None and source!=ref) or (refs is not None and source not in refs):continue
        for member in q.get('graph_members',(q,)):
            plan=member.get('route_plan')
            if plan is None or not getattr(plan,'merged_keys',()):continue
            identity=key(plan)
            absorbed=set(plan.merged_keys)-{identity}
            deleted.update(absorbed)
            for old in absorbed:
                rows.pop(old,None)
                for side in (0,1):angles.pop((old,side),None)
            rows[identity]=(*identity,*member['interval'])
            for side,manual in enumerate(member.get('manual_angles',(False,False))):
                if manual:
                    tangent=plan.tangent(member['interval'][side])
                    absolute=math.atan2(tangent[1],tangent[0])+math.pi/2+member['boundary_angles'][side]
                    angles[identity,side]=(*identity,side,absolute)
    return replace(settings,reconstruction_intervals=tuple(rows.values()),reconstruction_angles=tuple(angles.values()),
                   reconstruction_deleted=tuple(sorted(deleted)))
