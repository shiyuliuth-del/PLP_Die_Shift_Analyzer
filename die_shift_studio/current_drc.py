"""One current-pose DRC catalogue for the canvas and its location table.

Rigidly co-moving material is checked in its common nominal frame and the
witnesses are transformed once. Deformed copper uses the actual replacements.
Display visibility and statistical yield levels never change these binary rules.
"""
from collections import defaultdict
import math
import numpy as np
import shapely
from .resources import cancel_check
from .drc_rules import effective_rules


def _matrix(model,owner,states):
    if owner<0:return np.array([1.,0.,0.,1.,0.,0.])
    dx,dy,angle=states[owner];c=model.bound.centers[owner];co,si=np.cos(angle),np.sin(angle)
    rotation=np.array([[co,-si],[si,co]])
    return np.r_[rotation.ravel(),c-rotation@c+[dx,dy]]


def frame(checker,states,replacements,settings,progress,owners=None,scopes=None):
    from .drc_policy import enabled,ALL
    if not getattr(settings,'drc_checks',ALL):progress(100,'所有尺寸DRC类别均未启用');return []
    from .drc import separation_region
    from .drc_rules import effective_clearances
    m=checker.model;p=checker.p;clearances=effective_clearances(p,settings);dbu=m.data.dbu_um;states=np.asarray(states,float)
    def dependencies(values):return tuple(sorted(v for v in values if v>=0))
    def wanted(deps):return (scopes is None or deps in scopes) and (owners is None or bool(set(deps).intersection(owners)))
    replacements=replacements or {};overrides={};groups=defaultdict(list);items=[]
    for layer,rows in replacements.items():
        for row in rows:
            for i in row['indices']:overrides[layer,i]=row['geometry']
    if not hasattr(checker,'templates'):
        base=defaultdict(list)
        for node in m.nodes:
            role=m.roles[node['key']]
            if role in ('运动RDL','固定RDL'):base[node['key'].split('/')[0],node['net']].append(dict(node=node,role=role))
        checker.templates={key:(rows,shapely.union_all([r['node']['poly'] for r in rows])) for key,rows in base.items()}
    if not hasattr(checker,'rigid_candidates'):
        candidates=defaultdict(set)
        for link in getattr(m.connections,'links',()):
            a=(link['upper'],link['upper_index']);b=(link['lower'],link['lower_index'])
            for receiver,via in ((a,b),(b,a)):
                if m.roles.get(receiver[0]) in ('运动RDL','固定RDL') and m.roles.get(via[0]) in ('运动过孔','固定过孔'):
                    candidates[receiver].add(m.nodes[m.ids[via]]['owner'])
        checker.rigid_candidates=candidates
    proved={}
    for ref,g in overrides.items():
        node=m.nodes[m.ids[ref]];normalized=g.normalize()
        for owner in dict.fromkeys([node['owner'],*sorted(checker.rigid_candidates.get(ref,()))]):
            expected=m.moved(node['poly'],owner,states)
            # Verify coordinates, not area or bounding boxes. A copper edit
            # that merely resembles a translated source cannot reuse its DRC.
            if expected.normalize().equals_exact(normalized,dbu*1.e-7):proved[ref]=owner;break
    for key,(template,original) in checker.templates.items():
        groups[key]=[dict(r,changed=(r['node']['key'],r['node']['index']) in overrides and (r['node']['key'],r['node']['index']) not in proved,
                         motion_owner=proved.get((r['node']['key'],r['node']['index']),r['node']['owner'])) for r in template]
    nets=[]
    for count,(key,rows) in enumerate(groups.items()):
        cancel_check();progress(round(65*count/max(1,len(groups))),f'当前图形 DRC · 金属平面 {key[0]}')
        layers=tuple(sorted({r['node']['key'] for r in rows}));who={r['motion_owner'] for r in rows}
        rigid=not any(r['changed'] for r in rows) and len(who)==1;owner=next(iter(who)) if rigid else -1
        original=checker.templates[key][1]
        if not rigid:
            for r in rows:
                node=r['node'];ref=node['key'],node['index']
                r['geometry']=overrides[ref] if ref in overrides else m.moved(node['poly'],node['owner'],states)
        actual=m.moved(original,owner,states) if rigid else shapely.union_all([r['geometry'] for r in rows])
        rules={role:effective_rules(p,settings,role) for role in {r['role'] for r in rows}}
        refs=tuple(sorted((r['node']['key'],r['node']['index']) for r in rows))
        net=dict(key=key,rows=rows,original=original,geometry=actual,rigid=rigid,owner=owner,owners=who,
                 matrix=_matrix(m,owner,states) if rigid else None,layers=layers,refs=refs,space=max(s for w,s in rules.values()))
        nets.append(net)
        deps=dependencies(who)
        if not wanted(deps):continue
        seen=set()
        for role,(width,space) in rules.items():
            # Different declared rules on the same physical net are tested on
            # its complete copper, so artificial role seams are not DRC edges.
            region=shapely.union_all([r['node']['poly'] if rigid else r['geometry'] for r in rows if r['role']==role])
            for kind,g in checker.cached(original if rigid else actual,width if enabled(settings,'width') else 0.,space if enabled(settings,'space') else 0.,getattr(settings,'drc_min_angle_deg',0) if enabled(settings,'angle') else 0.):
                if len(rules)>1 and not g.intersects(region):continue
                world=m.moved(g,owner,states) if rigid else g;identity=kind,world.wkb
                if identity in seen:continue
                seen.add(identity)
                required=width if kind=='线宽不足' else space if kind=='线距不足' else getattr(settings,'drc_min_angle_deg',0)
                items.append(dict(layer=layers[0],layers=layers,geometry=world,kind=kind,net=key[1],refs=refs,
                                  owner=deps[0] if len(deps)==1 else -1,dependencies=deps,required_um=required if kind!='最小内角不足' else None,
                                  required_deg=required if kind=='最小内角不足' else None,_measurement_copper=actual))
    tree=shapely.STRtree([n['geometry'] for n in nets]);maximum=max((n['space'] for n in nets),default=0.)
    for i,a in enumerate(nets if enabled(settings,'space') else ()):
        cancel_check()
        for j in tree.query(a['geometry'],predicate='dwithin',distance=maximum):
            b=nets[int(j)]
            if j<=i or a['key'][0]!=b['key'][0]:continue
            deps=dependencies(a['owners']|b['owners'])
            if not wanted(deps):continue
            space=max(a['space'],b['space'])
            if not a['geometry'].dwithin(b['geometry'],space):continue
            same=a['rigid'] and b['rigid'] and np.allclose(a['matrix'],b['matrix'],rtol=0,atol=dbu*1e-7)
            if same:
                pair_key=(a['refs'],b['refs'],space)
                if pair_key not in checker.pairs:checker.pairs[pair_key]=separation_region(a['original'],b['original'],space,dbu)
                bad=checker.pairs[pair_key]
            else:bad=separation_region(a['geometry'],b['geometry'],space,dbu)
            if same:bad=m.moved(bad,a['owner'],states)
            if not bad.is_empty:
                layers=tuple(sorted(set(a['layers']+b['layers'])))
                items.append(dict(layer=layers[0],layers=layers,geometry=bad,kind='异网短接 / 线距不足',
                                  nets=(a['key'][1],b['key'][1]),refs=a['refs']+b['refs'],required_um=space,_measurement_pair=(a['geometry'],b['geometry']),dependencies=deps,owner=deps[0] if len(deps)==1 else -1))
    if not hasattr(checker,'enclosures'):
        catalogue={}
        for link in getattr(m.connections,'links',()):
            a=(link['upper'],link['upper_index']);b=(link['lower'],link['lower_index'])
            for via,rdl in ((a,b),(b,a)):
                vr=m.roles.get(via[0]);rr=m.roles.get(rdl[0])
                if vr in ('运动过孔','固定过孔') and rr in ('运动RDL','固定RDL'):
                    catalogue.setdefault((via,rdl[0].split('/')[0],rr),set()).add(rdl)
        for record in m.records:
            for ref in m.connections.fixed_interfaces(record['key'],record['index']):
                catalogue.setdefault(((record['key'],record['index']),ref[0].split('/')[0],'固定RDL'),set()).add(tuple(ref))
        checker.enclosures=catalogue
    catalogue=checker.enclosures
    for (ref,plane,role),receivers in (catalogue.items() if enabled(settings,'enclosure') else ()):
        cancel_check();node=m.nodes[m.ids[ref]];owner=node['owner']
        deps=dependencies({owner,*[m.nodes[m.ids[target]]['owner'] for target in receivers]})
        if not wanted(deps):continue
        same=all((target not in overrides or target in proved) and proved.get(target,m.nodes[m.ids[target]]['owner'])==owner for target in receivers)
        enclosure=clearances.moving_annulus_um if role=='运动RDL' else clearances.annulus_um
        # Buffer BEFORE the rigid transform: rotating the tessellation after
        # buffering preserves a common motion exactly, unlike re-buffering it.
        required=node['poly'].buffer(enclosure/math.cos(math.pi/128),quad_segs=32)
        target=[]
        for receiver in receivers:
            original=m.nodes[m.ids[receiver]]
            target.append(original['poly'] if same else (overrides[receiver] if receiver in overrides else m.moved(original['poly'],original['owner'],states)))
        missing=(required if same else m.moved(required,owner,states)).difference(shapely.union_all(target))
        if missing.area>dbu*dbu:
            if same:missing=m.moved(missing,owner,states)
            layers=tuple(sorted({k for k,i in receivers}))
            items.append(dict(layer=layers[0],layers=layers,geometry=missing,kind='孔盘包围不足',owner=deps[0] if len(deps)==1 else -1,dependencies=deps,rdl_role=role,
                              source_layer=ref[0],source_index=ref[1],refs=(ref,*sorted(receivers)),
                              origin=list(m.moved(node['poly'],owner,states).centroid.coords[0]),required_um=enclosure,_measurement_enclosure=(m.moved(node['poly'],owner,states),shapely.union_all([m.moved(g,owner,states) for g in target]) if same else shapely.union_all(target))))
    from .edge_clearance import edge_violations
    from .width_keepout import violations as keepout_violations
    for row in [*(edge_violations(m,states,p,settings,replacements,owners) if enabled(settings,'board') else ()),*(keepout_violations(m,states,replacements) if enabled(settings,'keepout') else ())]:
        refs=[(row['layer'],i) for i in row.get('indices',(row.get('index',0),))]
        deps=dependencies({m.nodes[m.ids[ref]]['owner'] for ref in refs if ref in m.ids})
        if wanted(deps):items.append(dict(row,dependencies=deps,owner=deps[0] if len(deps)==1 else -1))
    # Mechanical clearances use the same signed dimensional tolerance and
    # report local witnesses rather than a whole DIE-sized error rectangle.
    moved=[m.moved(d,i,states) for i,d in enumerate(getattr(m,'case',{}).get('dies',()))]
    board=getattr(m,'case',{}).get('board');guard=max(dbu*1.e-6,1.e-9)
    for i,die in enumerate(moved):
        if wanted((i,)) and enabled(settings,'die_edge'):
            required=clearances.min_package_clearance_um
            bad=die.difference(board.buffer(-required))
            if not bad.is_empty:
                items.append(dict(layer='',layers=(),geometry=bad,kind='芯片到封装机械净距不足',owner=i,dependencies=(i,),refs=(),required_um=required,actual_um=die.distance(board.boundary) if board.covers(die) else -float(np.max(shapely.distance(shapely.points(shapely.get_coordinates(die.difference(board))),board),initial=0.))))
        for j in range(i):
            deps=(j,i)
            if not wanted(deps) or not enabled(settings,'die_gap'):continue
            required=clearances.min_die_gap_um
            if die.distance(moved[j])<required-guard or die.intersects(moved[j]):
                bad=shapely.union_all([die.intersection(moved[j].buffer(required+guard)),moved[j].intersection(die.buffer(required+guard))])
                if bad.is_empty:
                    from shapely.ops import nearest_points
                    bad=shapely.LineString(nearest_points(die,moved[j])).buffer(dbu)
                items.append(dict(layer='',layers=(),geometry=bad,kind='芯片间机械净距不足',owner=-1,dependencies=deps,refs=(),required_um=required,actual_um=die.distance(moved[j])))
    progress(100,f'当前图形 DRC 完成 · {len(items)} 组违规')
    return items


def locations(items,measure=True):
    """Exactly one table row and one locator per connected witness component."""
    rows=[];seen=set()
    for item in items:
        cancel_check()
        pending=[item['geometry']]
        while pending:
            geometry=pending.pop()
            if geometry.is_empty:continue
            if geometry.geom_type.startswith('Multi') or geometry.geom_type=='GeometryCollection':
                pending.extend(geometry.geoms);continue
            key=(tuple(sorted(item.get('layers',(item.get('layer',''),)))),item['kind'],geometry.normalize().wkb)
            if key in seen:continue
            seen.add(key)
            from .drc_actual import measured
            rows.append(dict({k:v for k,v in item.items() if not k.startswith('_measurement')},**(measured(item,geometry) if measure else {}),geometry=geometry,point=list(geometry.representative_point().coords[0])))
    return sorted(rows,key=lambda q:(q.get('layer',''),q['kind'],tuple(q['point'])))


def coupled_scopes(model,parameters,settings):
    """Conservative electrical dependencies, excluding provably remote planes."""
    from .statistics import mean_and_bounds
    groups=defaultdict(list)
    for node in model.nodes:
        if model.roles[node['key']] in ('运动RDL','固定RDL'):
            groups[node['key'].split('/')[0],node['net']].append(node)
    nets=[];count=len(model.case['dies']);scopes=set()
    # Prove remote mechanical pairs independent before building cross products.
    from .drc_rules import effective_clearances
    mechanical=effective_clearances(parameters,settings);envelopes=[]
    for i,die in enumerate(model.case['dies']):
        mean,bound=mean_and_bounds(parameters.for_die(i),count)
        radius=np.max(np.linalg.norm(shapely.get_coordinates(die)-model.bound.centers[i],axis=1))
        reach=np.linalg.norm(abs(mean[:2])+bound[:2])+2*radius*min(1.,(abs(mean[2])+bound[2])/2)
        x0,y0,x1,y1=die.bounds;envelopes.append(shapely.box(x0-reach,y0-reach,x1+reach,y1+reach))
    mechanical_tree=shapely.STRtree(envelopes)
    for i,g in enumerate(envelopes):
        for j in mechanical_tree.query(g,predicate='dwithin',distance=mechanical.min_die_gap_um+model.data.dbu_um):
            if j>i:scopes.add((i,int(j)))
    for (plane,net),nodes in groups.items():
        deps=tuple(sorted({n['owner'] for n in nodes if n['owner']>=0}))
        if len(deps)>1:scopes.add(deps)
        bounds=[]
        for node in nodes:
            g=node['poly'];owner=node['owner'];reach=0.
            if owner>=0:
                mean,bound=mean_and_bounds(parameters.for_die(owner),count)
                radius=np.max(np.linalg.norm(shapely.get_coordinates(g)-model.bound.centers[owner],axis=1))
                reach=np.linalg.norm(np.abs(mean[:2])+bound[:2])+2*radius*min(1.,(abs(mean[2])+bound[2])/2)
            x0,y0,x1,y1=g.bounds;bounds.append(shapely.box(x0-reach,y0-reach,x1+reach,y1+reach))
        nets.append((plane,deps,shapely.union_all(bounds)))
    margin=max(parameters.line_space_um,parameters.wafer_line_space_um)-settings.drc_tolerance_um+1.
    tree=shapely.STRtree([v[2] for v in nets])
    for i,(plane,deps,envelope) in enumerate(nets):
        for j in tree.query(envelope,predicate='dwithin',distance=margin):
            other=nets[int(j)]
            scope=tuple(sorted(set(deps+other[1])))
            if j>i and plane==other[0] and len(scope)>1:scopes.add(scope)
    return sorted(scopes)
