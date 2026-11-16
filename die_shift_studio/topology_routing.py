"""Final electrical guard and bounded connected routing of actual copper.

Width/enclosure are soft repair targets; distinct nets, physical via bodies,
connected components and holes are hard constraints. No warning artwork is
ever used as fabricated copper.
"""
import math
import numpy as np
import shapely
from .resources import cancel_check
from .reconstruction_route import parts


def signature(g):return len(parts(g)),sum(len(p.interiors) for p in parts(g))


def failure_message(rows):
    details='；'.join(f'{layer} #{index+1}：{reason}' for (layer,index),reason in rows[:8])
    return '当前姿态无法生成保持电连接、原拓扑且无异网接触的图形，未提交该结果；保留上一完成状态。'+details


def covers_ports(g,ports,tol):
    # Full enclosure is a process rule. Electrical continuity requires a real
    # positive-area contact, not merely coincident boundaries or a marker line.
    return all(p.intersection(g).area>tol*tol for p in ports)


def separate_pair(a,b,source_a,source_b,ports_a,ports_b,tol,space=0.):
    """Share the overlap corridor; clipping only one pad can be impossible."""
    overlap=a.intersection(b)
    if overlap.is_empty:return a,b
    location=max(shapely.get_parts(overlap),key=lambda g:g.area+tol*g.length).representative_point()
    center=np.asarray(location.coords[0]);directions=[]
    if ports_a and ports_b:
        pa=min(ports_a,key=lambda g:g.distance(location));pb=min(ports_b,key=lambda g:g.distance(location))
        directions.append(np.asarray(pb.centroid.coords[0])-np.asarray(pa.centroid.coords[0]))
    from shapely.ops import nearest_points
    pa=nearest_points(source_a,location)[0];pb=nearest_points(source_b,location)[0]
    directions.append(np.asarray(pb.coords[0])-np.asarray(pa.coords[0]))
    directions.extend((np.array([1.,0.]),np.array([0.,1.])))
    rect=shapely.get_coordinates(shapely.minimum_rotated_rectangle(overlap))
    if len(rect)>2:directions.extend(np.diff(rect,axis=0)[:2])
    gap=max(tol*8,space);size=a.length+b.length+1;patch=overlap.buffer(gap+tol*8,quad_segs=4);best=None
    for normal in directions:
        length=np.linalg.norm(normal)
        if length<=tol:continue
        for sign in (-1.,1.):
            n=normal/length*sign;u=np.array([-n[1],n[0]])
            for shift in (0.,-.2,.2):
                c=center+shift*np.sqrt(max(overlap.area,tol*tol))*n
                edge0=c-n*gap/2;edge1=c+n*gap/2
                ha=shapely.Polygon([edge0-u*size,edge0+u*size,edge0+u*size-n*size,edge0-u*size-n*size])
                hb=shapely.Polygon([edge1-u*size,edge1+u*size,edge1+u*size+n*size,edge1-u*size+n*size])
                ga=shapely.union_all(parts(a.difference(patch.difference(ha))),grid_size=tol)
                gb=shapely.union_all(parts(b.difference(patch.difference(hb))),grid_size=tol)
                if (not ga.is_empty and not gb.is_empty and ga.is_valid and gb.is_valid
                    and signature(ga)==signature(source_a) and signature(gb)==signature(source_b)
                    and covers_ports(ga,ports_a,tol) and covers_ports(gb,ports_b,tol)
                    and not any(ga.intersects(p) for p in ports_b)
                    and not any(gb.intersects(p) for p in ports_a)
                    and not ga.intersects(gb)):
                    score=max((a.area-ga.area)/max(a.area,tol*tol),(b.area-gb.area)/max(b.area,tol*tol))
                    if best is None or score<best[0]:best=(score,ga,gb)
    return None if best is None else best[1:]


def connected_candidate(source,current,ports,obstacles,board,width,tol,space=0.):
    """Clip foreign copper, then join separated material through real free space."""
    expected=signature(source);gap=max(tol*4,space)
    forbidden=obstacles.buffer(gap,quad_segs=4) if not obstacles.is_empty else obstacles
    if any(p.difference(forbidden).area<=tol*tol for p in ports):return None
    if (current.is_valid and signature(current)==expected and covers_ports(current,ports,tol)
        and not current.intersects(obstacles) and (board is None or board.buffer(tol).covers(current))):return current
    full=shapely.union_all([current,*ports],grid_size=tol)
    if not forbidden.is_empty:full=full.difference(forbidden)
    if board is not None:full=full.intersection(board)
    full=shapely.union_all(parts(shapely.make_valid(full)),grid_size=tol)
    if not covers_ports(full,ports,tol):return None
    # A new hole inside originally solid copper may be a folded overlap. Fill
    # it only if the area contains no foreign net or other hard obstacle.
    if expected[1]==0:
        fills=[shapely.Polygon(h) for p in parts(full) for h in p.interiors]
        if fills and all(not h.intersects(forbidden) for h in fills):full=shapely.union_all([full,*fills],grid_size=tol)
    def acceptable(g):
        return (not g.is_empty and g.is_valid and signature(g)==expected and covers_ports(g,ports,tol)
                and not g.intersects(obstacles) and (board is None or board.buffer(tol).covers(g)))
    if acceptable(full):return full
    # Keep every material component and its terminal identity. A connector is
    # accepted only when it decreases the component count without adding holes.
    if len(parts(full))<=expected[0] or expected[0]!=1:return None
    bounds=shapely.union_all([source,full]).bounds
    for padding in (max(width*2,tol*16),max(width*8,tol*32)):
        region=shapely.box(bounds[0]-padding,bounds[1]-padding,bounds[2]+padding,bounds[3]+padding)
        if board is not None:region=region.intersection(board)
        free=region.difference(forbidden);working=full
        for _ in range(max(1,len(parts(full))-1)):
            cancel_check();components=parts(working)
            if len(components)==1:break
            pairs=sorted((a.distance(b),i,j) for i,a in enumerate(components) for j,b in enumerate(components) if i<j)
            joined=None
            for _,i,j in pairs:
                for diameter in dict.fromkeys((width,width*.75,width*.5,width*.25,width*.125,max(tol*8,width*.0625),tol*4)):
                    cancel_check();radius=max(tol*2,diameter/2);core=free.buffer(-radius,quad_segs=4)
                    a=components[i].intersection(core);b=components[j].intersection(core)
                    if a.is_empty or b.is_empty:continue
                    domain=next((p for p in parts(core) if p.intersects(a) and p.intersects(b)),None)
                    if domain is None:continue
                    ends=shapely.shortest_line(a.intersection(domain),b.intersection(domain))
                    from .reconstruction_route import trace
                    line=trace(domain,ends.coords[0],ends.coords[-1],diameter)
                    if line is None:continue
                    wire=line.buffer(radius,quad_segs=8,join_style=1)
                    candidate=shapely.union_all([working,wire],grid_size=tol)
                    if (candidate.is_valid and len(parts(candidate))<len(components)
                        and signature(candidate)[1]==expected[1] and not candidate.intersects(obstacles)
                        and covers_ports(candidate,ports,tol)):
                        joined=candidate;break
                if joined is not None:break
            if joined is None:break
            working=joined
        if acceptable(working):return working
    return None


def _adopt(rows,geometry,tol,reason):
    """Keep material regions disjoint and attach compensation to its real A area."""
    def material(g):return shapely.union_all(parts(g))
    def overlay(a,b,op):
        try:return material(getattr(shapely,op)(a,b))
        except shapely.errors.GEOSException:
            # Display partition only: shared DBU overlay, never a repaired
            # substitute for the already electrically-validated copper.
            return material(getattr(shapely,op)(a,b,grid_size=tol))
    before=shapely.union_all([g for q in rows for g in q['regions'].values()])
    if before.symmetric_difference(geometry).area<=tol*tol:reason='原铜已包含当前全部连接端，保留原图；未伪装成型变'
    fixed=overlay(shapely.union_all([q['regions']['B'] for q in rows]),geometry,'intersection')
    moving=overlay(overlay(shapely.union_all([q['regions']['C'] for q in rows]),geometry,'intersection'),fixed,'difference')
    remaining=overlay(geometry,shapely.union_all([fixed,moving]),'difference');assigned=shapely.Polygon()
    regions=[]
    for q in rows:
        a=overlay(overlay(remaining,q['regions']['A'].buffer(tol*4),'intersection'),assigned,'difference')
        regions.append(a);assigned=overlay(assigned,a,'union')
    for g in parts(overlay(remaining,assigned,'difference')):
        index=min(range(len(rows)),key=lambda i:rows[i]['regions']['A'].distance(g) if not rows[i]['regions']['A'].is_empty else rows[i].get('missing_region',g).distance(g))
        regions[index]=overlay(regions[index],g,'union')
    for q,a in zip(rows,regions):
        q.update(regions=dict(A=a,B=fixed,C=moving),bridge=a,fixed=fixed,moved=moving,
                 bridge_area_um2=a.area,fixed_area_um2=fixed.area,moved_area_um2=moving.area,
                 connection_failed=False,valid=True,routing_recovered=True,routing_note=reason,
                 warning=reason,compensation_region=before.symmetric_difference(geometry))


def resolve(r,replacements,pieces,states):
    """Deterministic whole-pose collision resolution; immutable source identities."""
    out={k:[dict(v) for v in values] for k,values in replacements.items()}
    pieces=[dict(q,regions=dict(q['regions'])) for q in pieces];by_ref={};ports={}
    for q in pieces:by_ref.setdefault((q['layer'],q['index']),[]).append(q)
    inputs=getattr(r,'_topology_inputs',None)
    if inputs is None:
        original={};nets={};terminals={}
        for key,refs in r.rules.nets.items():
            for ref in refs:
                original[ref]=r.model.nodes[r.model.ids[ref]]['poly'];nets[ref]=key;terminals[ref]=[]
                for via,owner,required in r.rules.vias[key]:
                    physical=r.model.nodes[r.model.ids[via]]['poly']
                    if original[ref].intersection(physical).area>r.tol*r.tol:terminals[ref].append((owner,physical))
        r._topology_inputs=inputs=(original,nets,terminals)
    original,nets,terminals=inputs;objects=dict(original)
    for layer,rows in out.items():
        for row in rows:
            for i in row['indices']:objects[layer,i]=row['geometry']
    mutable={ref for ref,rows in by_ref.items() if any(not q.get('ignored') for q in rows)}
    for ref in objects:ports[ref]=[r.model.moved(physical,owner,states) for owner,physical in terminals[ref]]
    changed=set();blocked=[];external={};immutable={}
    for ref,values in ports.items():
        external.setdefault(nets[ref][0],[]).extend((nets[ref],g) for g in values)
    for node in r.model.nodes:
        if r.model.roles[node['key']]=='固定RDL':continue
        key=(node['key'].split('/')[0],node['net']);g=r.model.moved(node['poly'],node['owner'],states)
        external.setdefault(key[0],[]).append((key,g));immutable.setdefault(key[0],[]).append((node,g))
    external={plane:(values,shapely.STRtree([g for net,g in values])) for plane,values in external.items()}
    def foreign_hits(ref,g):
        table=external.get(nets[ref][0])
        if table is None:return []
        values,tree=table
        return [values[int(i)][1] for i in tree.query(g,predicate='intersects') if values[int(i)][0]!=nets[ref]]
    def obstacles(ref):
        b=shapely.union_all([original[ref],objects[ref]]).bounds;pad=r._rule_width*8+r.tol*32
        box=shapely.box(b[0]-pad,b[1]-pad,b[2]+pad,b[3]+pad)
        return shapely.union_all([g for other,g in objects.items() if nets[other][0]==nets[ref][0] and nets[other]!=nets[ref] and g.intersects(box)]+[r.rules.keepout.intersection(box),*foreign_hits(ref,box)])
    def valid(ref,g):return g.is_valid and signature(g)==signature(original[ref]) and covers_ports(g,ports[ref],r.tol)
    def attempt(ref,reason):
        if ref not in mutable:return None
        foreign=obstacles(ref)
        for space in dict.fromkeys((r.rule_space,r.rule_space*.5,0.)):
            candidate=connected_candidate(original[ref],objects[ref],ports[ref],foreign,r.rules.board,r._rule_width,r.tol,space)
            if candidate is not None:return candidate,reason
        return None
    # Recover electrical discontinuities first; the actual posed foreign copper
    # is an obstacle even when full native DRC artwork was not requested.
    for ref in sorted(mutable):
        cancel_check()
        if valid(ref,objects[ref]) and not foreign_hits(ref,objects[ref]) and not any(q.get('connection_failed') for q in by_ref[ref]):continue
        candidate=attempt(ref,'保连通补偿：已调整连接轮廓，工艺规则另检')
        if candidate is not None:
            objects[ref]=candidate[0];changed.add(ref);_adopt(by_ref[ref],candidate[0],r.tol,candidate[1])
    # Removing one side of an overlap cannot create a new foreign contact.
    # A rerouted candidate is checked against every current neighbour before use.
    refs=list(objects)
    for _ in range(max(1,len(refs)*2)):
        cancel_check();values=[objects[ref] for ref in refs];tree=shapely.STRtree(values);pairs=[]
        for i,a in enumerate(values):
            for j in tree.query(a,predicate='intersects'):
                if j>i and nets[refs[i]][0]==nets[refs[int(j)]][0] and nets[refs[i]]!=nets[refs[int(j)]]:pairs.append((refs[i],refs[int(j)]))
        if not pairs:break
        progressed=False
        for left,right in pairs:
            if not objects[left].intersects(objects[right]):continue
            # Most collisions are overlapping pad shoulders. Try one shared
            # corridor before expensive whole-net visibility routing.
            if left in mutable and right in mutable:
                divided=None
                for space in dict.fromkeys((r.rule_space,r.rule_space*.5,0.)):
                    candidate=separate_pair(objects[left],objects[right],original[left],original[right],ports[left],ports[right],r.tol,space)
                    if candidate is not None and all(not foreign_hits(ref,g) for ref,g in zip((left,right),candidate)):
                        divided=candidate;break
                if divided is not None:
                    for ref,g in zip((left,right),divided):
                        objects[ref]=g;changed.add(ref);_adopt(by_ref[ref],g,r.tol,'共同通道补偿：两侧轮廓同时收窄，工艺规则另检')
                    progressed=True;continue
            candidates=[]
            for ref in (left,right):
                candidate=attempt(ref,'异网避让补偿：局部收窄或绕接，工艺规则另检')
                if candidate is not None:candidates.append((objects[ref].symmetric_difference(candidate[0]).area,ref,*candidate))
            if not candidates:continue
            _,ref,g,reason=min(candidates,key=lambda item:(item[0],item[1]));objects[ref]=g;changed.add(ref);_adopt(by_ref[ref],g,r.tol,reason);progressed=True
        if not progressed:break
    for ref in sorted(objects):
        if not valid(ref,objects[ref]):blocked.append((ref,'无法获得包含全部过孔且保持原拓扑的连通铜'))
        if foreign_hits(ref,objects[ref]):blocked.append((ref,'固定RDL仍接触其他网络的实际过孔或同层金属'))
    values=[objects[ref] for ref in refs];tree=shapely.STRtree(values)
    for i,a in enumerate(values):
        for j in tree.query(a,predicate='intersects'):
            if j>i and nets[refs[i]][0]==nets[refs[int(j)]][0] and nets[refs[i]]!=nets[refs[int(j)]]:
                blocked.extend(((refs[i],'异网实体接触仍无法避让'),(refs[int(j)],'异网实体接触仍无法避让')))
    for plane,values in immutable.items():
        nodes=[n for n,g in values]
        if len({n['owner'] for n in nodes})<2:continue
        actual=[g for n,g in values];tree=shapely.STRtree(actual)
        for i,g in enumerate(actual):
            for j in tree.query(g,predicate='intersects'):
                if j>i and nodes[i]['net']!=nodes[int(j)]['net']:
                    blocked.extend((((n['key'],n['index']),'同层异网金属实体接触，无法通过固定RDL改线消除') for n in (nodes[i],nodes[int(j)])))
    for ref,reason in blocked:
        for q in by_ref.get(ref,()):q.update(connection_failed=True,valid=False,warning=reason,hard_unroutable=True)
    for layer,rows in out.items():
        for row in rows:
            if len(row['indices'])==1:row['geometry']=objects[layer,row['indices'][0]]
    return out,pieces,tuple(sorted(changed)),tuple(sorted(set(blocked)))


def spacing(r,out,pieces):
    geos=r.rules.geometries(out);tables={}
    for key in {r.rules.refnet[q['layer'],q['index']] for q in pieces}:
        others=[(k,g) for k,g in geos.items() if k[0]==key[0] and k!=key]
        tables[key]=(others,shapely.STRtree([g for k,g in others]))
    for q in pieces:
        key=r.rules.refnet[q['layer'],q['index']];others,tree=tables[key]
        if not others:continue
        i=int(tree.nearest(geos[key]));net,other=others[i];distance=geos[key].distance(other)
        q.update(clearance_um=distance,conflict=distance<r.rule_space-r.tol,conflict_net=net[1],
                 conflict_link=shapely.get_coordinates(shapely.shortest_line(geos[key],other)).tolist())
