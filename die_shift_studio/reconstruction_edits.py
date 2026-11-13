"""Validated nominal-source edits shared across styles and preview poses."""
import math
from dataclasses import replace


def key(plan):return (*plan.key,plan.identity)


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
    angles,manual=blade_angles(settings,plan,part['a'],part['b'],part.get('angles',(0.,0.)))
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


def change_zone(window,action,piece=None,ref=None,point=None,side=None):
    """Apply one source-bound edit; all poses share the resulting settings key."""
    import uuid
    cfg=window.view_settings
    if piece is not None:ref=(piece['layer'],piece['index'])
    if ref is None:return
    ignored=set(cfg.reconstruction_ignored);forced=set(cfg.reconstruction_forced)
    if action=='rotate_reset':
        updated=rotate(cfg,piece,side,None)
    elif action=='delete':
        plan=piece.get('route_plan')
        if plan is None:return
        deleted=set(cfg.reconstruction_deleted);deleted.add(key(plan))
        present=[q for q in (window.current_frame or {}).get('reconstruction',{}).get('pieces',()) if (q['layer'],q['index'])==ref and q.get('route_plan') is not None]
        if all(key(q['route_plan']) in deleted for q in present):ignored.add(ref)
        updated=replace(cfg,reconstruction_deleted=tuple(sorted(deleted)),reconstruction_ignored=tuple(sorted(ignored)))
    elif action=='add':
        ignored.discard(ref);forced.add(ref)
        current=[q for q in (window.current_frame or {}).get('reconstruction',{}).get('pieces',()) if (q['layer'],q['index'])==ref and q.get('route_plan') is not None]
        added=cfg.reconstruction_added
        # Restoring an empty/ignored shape creates its normal automatic zones.
        # Additional clicks split the nearest existing route into serial zones.
        deleted=cfg.reconstruction_deleted
        intervals=list(cfg.reconstruction_intervals)
        if current:
            import shapely
            from .selection import inverse_geometry
            target=min(current,key=lambda q:q['bridge'].distance(shapely.Point(point)))
            plan=target['route_plan'];base_id=plan.identity.split('~')[0]
            a,b=target['interval'];position=project_click(target,point)
            guard=max(plan.tolerance*32,min(plan.guard,(b-a)*.03))
            position=min(b-guard*4,max(a+guard*4,position))
            if b-a<=guard*8:
                window.statusBar().showMessage('该动区没有足够长度再拆出一个有效动区');return
            identity=uuid.uuid4().hex[:12];new_id=base_id+'~'+identity
            scoped=[q for q in current if q['route_plan'].identity.split('~')[0]==base_id]
            outer_a=min(q['interval'][0] for q in scoped);outer_b=max(q['interval'][1] for q in scoped)
            saved={tuple(r[:5]):r for r in intervals}
            for q in scoped:
                identity_key=key(q['route_plan']);left,right=q['interval']
                if q is target:right=position-guard
                saved[identity_key]=(*identity_key,left,right)
            saved[(*plan.key,new_id)]=(*plan.key,new_id,position+guard,b)
            intervals=list(saved.values())
            added=(*added,(*ref,float(plan.route.interpolate(position).x),float(plan.route.interpolate(position).y),identity,base_id,outer_a,outer_b))
        else:
            added=tuple(row for row in added if row[:2]!=ref)+((*ref,float(point[0]),float(point[1]),uuid.uuid4().hex[:12],'seed'),)
        updated=replace(cfg,reconstruction_added=added,reconstruction_deleted=deleted,reconstruction_intervals=tuple(intervals),
                        reconstruction_ignored=tuple(sorted(ignored)),reconstruction_forced=tuple(sorted(forced)))
    else:return
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
