"""Exact source-clipped, optionally rotated manual reconstruction regions."""
import math
import numpy as np
import shapely

BOX_HISTORY_FIELDS=('reconstruction_boxes','reconstruction_angle_boxes','reconstruction_box_order')


def normalize(rows):
    out=[]
    if not isinstance(rows,(tuple,list)):raise ValueError('框选动区须为列表')
    for layer,vertices in rows:
        points=np.asarray(vertices,float)
        if points.shape!=(4,2) or not np.isfinite(points).all() or np.max(abs(points))>1e12:raise ValueError('框选动区须为四个有限坐标')
        polygon=shapely.Polygon(points)
        if not polygon.is_valid or polygon.area<=0:raise ValueError('框选动区必须为有效四边形')
        out.append((str(layer),tuple(tuple(map(float,p)) for p in points)))
    return tuple(out)

def box_order(settings):
    free,aligned=len(settings.reconstruction_boxes),len(settings.reconstruction_angle_boxes)
    order=settings.reconstruction_box_order
    return order or ('free',)*free+('aligned',)*aligned


def ordered_boxes(settings):
    rows={'free':iter(settings.reconstruction_boxes),'aligned':iter(settings.reconstruction_angle_boxes)}
    return tuple((mode,*next(rows[mode])) for mode in box_order(settings))


def route_box_spans(plan,polygon):
    """Separate visits to a box by arclength, even when they share one net."""
    spans=[]
    for line in shapely.get_parts(plan.route.intersection(polygon)):
        if line.geom_type!='LineString' or line.length<=plan.tolerance:continue
        ends=[plan.route.project(shapely.Point(line.coords[i])) for i in (0,-1)]
        spans.append(tuple(sorted(ends)))
    merged=[]
    for a,b in sorted(spans):
        if merged and a<=merged[-1][1]+plan.tolerance:
            merged[-1]=(merged[-1][0],max(b,merged[-1][1]))
        else:merged.append((a,b))
    return merged


def partition(plan,polygon,span=None):
    from .reconstruction_route import parts
    active=plan.source.intersection(polygon)
    if span is not None and active.geom_type!='Polygon':
        from shapely.ops import substring
        trace=substring(plan.route,*span)
        active=shapely.union_all([g for g in parts(active) if g.intersection(trace).length>plan.tolerance])
    if active.is_empty or active.area<=plan.tolerance**2:return None
    if span is None:
        marks=shapely.line_locate_point(plan.route,shapely.points(shapely.get_coordinates(plan.route.intersection(polygon))))
        if not len(marks):marks=shapely.line_locate_point(plan.route,shapely.points(shapely.get_coordinates(active)))
        span=float(marks.min()),float(marks.max())
    a,b=span
    moving=[];fixed=[];ambiguous=False;terminals=getattr(plan,'terminal_vias',((plan.land,),tuple(plan.anchors)))
    moving_terminal=shapely.union_all(terminals[0]);anchors=shapely.union_all(terminals[1])
    for g in parts(plan.source.difference(active)):
        m=g.intersection(moving_terminal).area>plan.tolerance**2;f=g.intersection(anchors).area>plan.tolerance**2
        if m and f:ambiguous=True
        if m or (not f and plan.route.project(g.representative_point())<(a+b)/2):moving.append(g)
        else:fixed.append(g)
    c=shapely.union_all(moving);d=shapely.union_all(fixed)
    # Rectangular box edges are the authoritative cut lines. Never line-merge
    # different box sides into an L-shaped or zig-zag reconstruction boundary.
    def seam(material,s):
        edges=[]
        segments=[(u,v) for part in shapely.get_parts(polygon) if part.geom_type=='Polygon' for xy in [np.asarray(part.exterior.coords)] for u,v in zip(xy[:-1],xy[1:])]
        # Boolean intersections may round a rotated side by a few ulps. Do
        # not substitute a pad point merely because two nominal lines miss.
        rings=[np.asarray(r.coords) for g in parts(material) for r in (g.exterior,*g.interiors)]
        if not rings:return shapely.LineString()
        starts=np.concatenate([r[:-1] for r in rings]);ends=np.concatenate([r[1:] for r in rings])
        eps=max(plan.tolerance*1e-5,32*np.spacing(max(1.,float(np.max(np.abs(starts))))))
        for u,v in segments:
            length=np.linalg.norm(v-u)
            if length<=eps:continue
            axis=(v-u)/length;normal=np.array([-axis[1],axis[0]])
            mask=(abs((starts-u)@normal)<=eps)&(abs((ends-u)@normal)<=eps)
            if not mask.any():continue
            spans=np.sort(np.column_stack(((starts[mask]-u)@axis,(ends[mask]-u)@axis)),axis=1).clip(0,length)
            runs=[]
            for lo,hi in sorted(map(tuple,spans)):
                if hi-lo<=eps:continue
                if runs and lo<=runs[-1][1]+eps:runs[-1][1]=max(hi,runs[-1][1])
                else:runs.append([lo,hi])
            edges.extend(shapely.LineString([u+lo*axis,u+hi*axis]) for lo,hi in runs if hi-lo>plan.tolerance)
        return min(edges,key=lambda g:(g.distance(plan.route.interpolate(s)),-g.length)) if edges else shapely.LineString()
    near=seam(c,a) if not c.is_empty else shapely.LineString()
    far=seam(d,b) if not d.is_empty else shapely.LineString()
    def blade_angle(edge,s):
        if edge.is_empty or edge.geom_type!='LineString':return 0.
        u=np.asarray(edge.coords[-1])-edge.coords[0];v=plan.tangent(s);normal=np.array([-v[1],v[0]])
        angle=math.atan2(u[1],u[0])-math.atan2(normal[1],normal[0])
        return (angle+math.pi/2)%math.pi-math.pi/2
    allow_pad_point=getattr(plan,'allows_pad_point',True)
    whole_moving_pad=not allow_pad_point and not plan.land.is_empty and plan.land.difference(active).area<=plan.tolerance**2
    if near.is_empty or whole_moving_pad:
        if not allow_pad_point:
            # The selection covered the moving endpoint. Re-establish the nearest
            # legal straight cut and preserve the far blade angle where possible.
            if far.is_empty:return None
            restored=plan.moving_line_partition(b,(0.,blade_angle(far,b)))
            if restored is None:return None
            return dict(restored,manual_angles=(False,True),strict_box=polygon,
                        box_invalid=ambiguous,box_complex=restored['A'].geom_type!='Polygon')
        near=shapely.Point(plan.route.coords[0]);a=0.
    # Trapped boxes remain visible diagnostics with no fixed-side dot.
    if far.is_empty:ambiguous=True
    angles=(blade_angle(near,a),blade_angle(far,b))
    return dict(A=active,B=d,C=c,near=near,far=far,a=a,b=b,angles=angles,manual_angles=(True,True),strict_box=polygon,
                box_invalid=ambiguous,box_complex=active.geom_type!='Polygon' or near.geom_type not in ('LineString','Point') or far.geom_type not in ('LineString','Point'))


def move_side(settings,plan,press,cursor,visible_box=None):
    """Move only the pressed side of its source rectangle in nominal coordinates."""
    from dataclasses import replace
    choices=[];press=np.asarray(press,float);cursor=np.asarray(cursor,float)
    for i,(layer,verts) in enumerate(settings.reconstruction_boxes):
        if layer!=plan.layer or not plan.source.intersects(shapely.Polygon(verts)):continue
        xy=np.asarray(verts,float)
        for j in range(4):
            edge=shapely.LineString([xy[j],xy[(j+1)%4]])
            choices.append((edge.distance(shapely.Point(press)),i,j))
    if not choices:return None
    distance,i,j=min(choices);rows=list(settings.reconstruction_boxes);layer,verts=rows[i];xy=np.array(verts,float)
    if distance>plan.tolerance*4 and visible_box is not None and visible_box.geom_type=='Polygon':
        contour=np.asarray(visible_box.exterior.coords[:-1],float)
        if contour.shape==(4,2):
            xy=contour.copy()
            j=min(range(4),key=lambda n:shapely.LineString([xy[n],xy[(n+1)%4]]).distance(shapely.Point(press)))
    a,b=j,(j+1)%4;v=xy[b]-xy[a];n=np.array([-v[1],v[0]])/np.linalg.norm(v)
    thickness=(xy[(j+2)%4]-xy[b])@n
    if thickness<0:n=-n;thickness=-thickness
    xy[[a,b]]+=min(float((cursor-press)@n),thickness-plan.tolerance*2)*n
    rows[i]=(layer,tuple(map(tuple,xy)));cfg=replace(settings,reconstruction_boxes=tuple(rows))
    bounds=shapely.union_all([shapely.Polygon(v) for k,v in rows if k==layer and plan.source.intersects(shapely.Polygon(v))]);part=usable_partition(plan,bounds)
    return (cfg,part) if part is not None else None


def box_list_by_source(model, rows):
    """Return disjoint connected box polygons per intersected source ref."""
    from .reconstruction_route import parts
    by_layer={}
    for layer, xy in rows:
        if layer in model.cache.layers:
            by_layer.setdefault(layer,[]).append(shapely.Polygon(xy))
    result={}
    for layer, boxes in by_layer.items():
        geometries=model.cache.layers[layer]['geometry']
        tree=shapely.STRtree(geometries)
        i,j=tree.query(boxes,predicate='intersects')
        for box,ref in zip(i,j):
            result.setdefault((layer,int(ref)),[]).append(boxes[int(box)])
    return {ref:tuple(parts(shapely.union_all(areas))) for ref,areas in result.items()}

def ordered_boxes_by_source(model,settings):
    # Preserve the legacy pre-merge for jobs that never recorded mixed ordering.
    if not settings.reconstruction_box_order and not settings.reconstruction_box_starts:
        found={}
        for is_free,rows in ((True,settings.reconstruction_boxes),(False,settings.reconstruction_angle_boxes)):
            for ref,boxes in box_list_by_source(model,rows).items():
                found.setdefault(ref,[]).extend((is_free,b) for b in boxes)
        return found
    from collections import defaultdict
    by_layer=defaultdict(list);found=defaultdict(list)
    starts={(layer,i):start for layer,i,start in settings.reconstruction_box_starts}
    for sequence,(mode,layer,vertices) in enumerate(ordered_boxes(settings)):
        if layer in model.cache.layers:
            by_layer[layer].append((sequence,mode=='free',shapely.Polygon(vertices)))
    for layer,rows in by_layer.items():
        tree=shapely.STRtree(model.cache.layers[layer]['geometry'])
        i,j=tree.query([p for _,_,p in rows],predicate='intersects')
        for box_idx,source_idx in zip(i,j):
            ref=(layer,int(source_idx));sequence,is_free,polygon=rows[box_idx]
            if sequence>=starts.get(ref,0):found[ref].append((is_free,polygon))
    return found


def boxes_by_source(model, rows):
    return {ref:shapely.union_all(areas) for ref,areas in box_list_by_source(model, rows).items()}


def usable_partition(plan, polygon, span=None):
    """Keep valid cuts; extend trapped boxes across the local trace if needed."""
    initial = partition(plan, polygon, span)
    def good(part):
        if not part or part['box_invalid'] or part['box_complex']:
            return False
        # An empty outer cut is legitimate only when its terminal is included.
        terminals = getattr(plan, 'terminal_vias', ((plan.land,), tuple(plan.anchors)))
        for name, ports in zip(('near', 'far'), terminals):
            if part[name].geom_type == 'Point' and not any(part['A'].intersects(g) for g in ports):
                return False
        # A diagonal trace can enter through a corner of the rectangle. Each
        # real interface must be one straight cut, not just the longest leg
        # of an L-shaped contact left behind by the unexpanded rectangle.
        eps=plan.tolerance
        active=part['A'].boundary
        for side,face in (('C',part['near']),('B',part['far'])):
            if face.geom_type!='LineString' or part[side].is_empty:continue
            interface=active.intersection(part[side].boundary.buffer(eps))
            if interface.difference(face.buffer(eps*4)).length>eps*8:return False
        return not part['A'].is_empty
    if good(initial) or not plan.source.intersects(polygon):
        return initial

    xy = shapely.get_coordinates(polygon)
    s = sum(span)/2 if span is not None else plan.route.project(polygon.representative_point())
    tangent = np.asarray(plan.tangent(s))
    rect = shapely.get_coordinates(polygon.minimum_rotated_rectangle)
    edges = np.diff(rect,axis=0)
    lengths = np.linalg.norm(edges,axis=1)
    if not len(lengths) or min(lengths)<=0:return initial
    directions=edges/lengths[:,None]
    direction=directions[int(np.argmax(abs(directions@tangent)))]
    basis = np.column_stack((direction, [-direction[1], direction[0]]))
    center = np.asarray(plan.route.interpolate(s).coords[0])
    local = (xy-center)@basis
    lo, hi = local.min(axis=0), local.max(axis=0)
    span = np.maximum(hi-lo, plan.tolerance*4)
    # Extend width first, around a local route, without growing along the
    # entire connected plane. Grow length only if the selection cannot split
    # that source with a transverse cut alone.
    base = max(float(getattr(plan, 'width', 0.)), min(span)*.5, plan.tolerance*8)
    for across, along in ((.5,0),(1,0),(2,0),(4,0),(2,.5),(4,1),(8,2)):
        pad = np.array([along,across])*base
        a,b = lo-pad,hi+pad
        grown = shapely.Polygon(np.array([[a[0],a[1]],[b[0],a[1]],[b[0],b[1]],[a[0],b[1]]])@basis.T+center)
        local_span=None
        if span is not None:
            candidates=route_box_spans(plan,grown)
            local_span=next((pair for pair in candidates if pair[0]-plan.tolerance<=s<=pair[1]+plan.tolerance),None)
            if local_span is None:continue
        result = partition(plan, grown, local_span)
        if good(result):
            return result
    return initial



def aligned_partition(plan,polygon,span=None):
    """Use the selected copper to find the interval, leaving blade angles dynamic."""
    if span is None:
        selected=plan.route.intersection(polygon)
        if selected.is_empty:selected=plan.source.intersection(polygon)
        xy=shapely.get_coordinates(selected)
        if not len(xy):return None
        marks=shapely.line_locate_point(plan.route,shapely.points(xy))
        span=float(marks.min()),float(marks.max())
    lo,hi=span
    if hi-lo<plan.tolerance*2:lo-=plan.tolerance*8;hi+=plan.tolerance*8
    for expansion in (0.,plan.guard,plan.guard*4):
        a=plan.snap(max(0.,lo-expansion),0,plan.length,allow_pad=True)
        b=plan.snap(min(plan.length,hi+expansion),1,a,allow_pad=False)
        if b>a:
            result=plan.partition(a,b)
            if result is not None:return result
    # A route can end exactly on a shared ownership seam. Recede a
    # few grid cells so the fixed face remains a real section of copper.
    if hi>=plan.fixed_start-plan.tolerance*8:
        a=plan.snap(max(0.,lo),0,plan.length,allow_pad=True)
        b=min(hi,plan.fixed_start-plan.tolerance*16)
        if b>a:return plan.partition(a,b)
    return None
