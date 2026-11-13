"""All discrete reconstruction poses, factored by real changed copper dependencies.

One source feature is built only for its owning DIE digits. Net components and
pairs use envelopes of these ACTUAL geometries, including curved return bends.
The decision graph is shared by filtering, zone colours and selected callouts.
"""
from collections import defaultdict
from itertools import product
import threading
import math
import numpy as np
import shapely
from .resources import parallel_map,cancel_check,resource_profile,native_phase_profile
from .extreme_filter import PoseClause,ProblemIndex
from .reconstruction_route import EMPTY

PALETTE={'red':'#df737d','yellow':'#edbb77','green':'#b8d8c0','gray':'#d5d8dd','pending':'#edbb77'}
RANK={'gray':0,'green':1,'yellow':2,'pending':2,'red':3}


def zone_key(q):return q['layer'],int(q['index']),int(q['die']),q.get('route_identity')


def active_region(q):
    """The annotation never falls back to the whole parent GDS polygon."""
    g=q['regions']['A']
    if not g.is_empty:return g
    # Missing connector is a warning band, not the retained rigid copper.
    return q.get('missing_region',EMPTY) if q.get('connection_failed') else q.get('nominal_regions',{}).get('A',EMPTY)


def support(q,tol,via=False,original=False):
    """Only the transition material determines its colour, never the rigid sides."""
    region=q.get('nominal_regions',{}).get('A',EMPTY) if original else active_region(q)
    key='_zone_original_support' if original else '_zone_current_support'
    cached=q.get(key)
    if cached is not None and cached[0]==tol and cached[1] is region:return cached[2]
    result=region.buffer(tol*2) if not region.is_empty else EMPTY
    q[key]=(tol,region,result)
    return result


def associate(pieces,violations,tol):
    """Attribute pre/post rule witnesses only where they intersect the A region."""
    found={zone_key(q):[] for q in pieces}
    for q in pieces:
        shape=support(q,tol);original=None
        for row in violations:
            own=(q['layer'].split('/')[0],q.get('net'));net=row.get('net')
            affected=(net,) if net and isinstance(net[0],str) else net
            if affected is not None and own not in affected:continue
            subject=shape
            if row.get('phase')=='original':
                if original is None:original=support(q,tol,original=True)
                subject=original
            g=row['geometry'];layers=row.get('layers',(q['layer'],))
            if q['layer'] in layers and not subject.is_empty and subject.intersects(g):
                found[zone_key(q)].append(dict(row,geometry=g.intersection(subject),kind=('原始动区：' if row.get('phase')=='original' else '')+row['kind']))
        if q.get('connection_failed',False):
            found[zone_key(q)].append(dict(kind=q.get('warning') or '连接未完成',geometry=active_region(q),connection=True,layers=(q['layer'],)))
    return found


def current_failures(frame):
    out={}
    for q in frame.get('pieces',()):
        issues=q.get('rule_witnesses',())
        if issues or q.get('connection_failed',False):out[zone_key(q)]=tuple(q.get('problem_reasons',()))
    return out


def scene_failure(frame):
    return bool(frame.get('violations')) or bool(current_failures(frame))


def color_for(q,bank,nominal,level,inside=True):
    key=zone_key(q);row=(bank or {}).get('zones',{}).get(key)
    bad=bool(q.get('rule_witnesses')) or bool(q.get('connection_failed'))
    # Bound must refer to this exact geometry/configuration and continuous law.
    low=bool(row and row['has_failure'] and row.get('probability',{}).get('certified') and inside and 0<level<math.inf and (bank or {}).get('scale_nsigma',level)==level and row['probability']['upper']<=row['probability']['threshold'])
    if nominal and row and row['has_failure']:
        if not low:return 'red'
        # Aggregate the colours of ALL poses: safe poses remain yellow when a
        # different pose fails, so yellow outranks low-probability gray.
        return 'yellow' if row['failing_states']<(bank or {}).get('package_states',math.inf) else 'gray'
    if not nominal and bad:return 'gray' if low else 'red'
    if row and row['complete']:
        if row['has_failure']:return 'yellow'
        return 'green' if q.get('valid',True) else 'pending'
    return 'red' if bad else 'pending'


def _compact(q):
    names=('layer','index','die','net','via_refs','route_identity','regions','nominal_regions','missing_region','moving_terminals','moved_cut','extra_cut','connection_failed','valid','warning','interval','boundary_angles')
    return {k:q[k] for k in names if k in q}


def _component_groups(r,bundles):
    groups=[]
    for key,refs in r.rules.nets.items():
        bounds=[bundles[ref]['envelope'] for ref in refs];tree=shapely.STRtree(bounds);left=set(range(len(refs)))
        while left:
            indices=[];todo=[left.pop()]
            while todo:
                i=todo.pop();indices.append(i)
                hits=set(map(int,tree.query(bounds[i],predicate='dwithin',distance=r.rules.space))) & left
                left-=hits;todo.extend(hits)
            selected=[refs[i] for i in sorted(indices)]
            original=shapely.union_all([r.model.nodes[r.model.ids[ref]]['poly'] for ref in selected])
            vias=[v for v in r.rules.vias[key] if r.model.nodes[r.model.ids[v[0]]]['poly'].intersects(original)]
            groups.append((key,selected,vias))
    return groups


def compatible_candidates(a,b,space):
    """Join shared DIE digits BEFORE spatial comparison, not after an N² query."""
    common=tuple(sorted(set(a['owners']) & set(b['owners'])))
    if not common:
        block=max(1,min(32,2_000_000//max(1,len(b['geometries']))))
        for offset in range(0,len(a['geometries']),block):
            cancel_check();x,y=b['tree'].query(a['geometries'][offset:offset+block],predicate='dwithin',distance=space)
            yield x+offset,y
        return
    def groups(t):
        cache=t.setdefault('joins',{})
        if common not in cache:
            cols=[t['owners'].index(o) for o in common];out=defaultdict(list)
            for i,ds in enumerate(t['digits'][:,cols]):out[tuple(ds)].append(i)
            cache[common]={ds:np.asarray(ids,int) for ds,ids in out.items()}
        return cache[common]
    aa,bb=groups(a),groups(b)
    for digits,ia in aa.items():
        cancel_check();ib=bb.get(digits)
        if ib is None:continue
        if len(ia)==1 or len(ib)==1:
            ix=np.repeat(ia,len(ib));iy=np.tile(ib,len(ia))
            keep=shapely.dwithin(a['geometries'][ix],b['geometries'][iy],space)
            yield ix[keep],iy[keep]
        else:
            cache=b.setdefault('join_trees',{});key=(common,digits);tree=cache.get(key)
            if tree is None:tree=shapely.STRtree(b['geometries'][ib]);cache[key]=tree
            x,y=tree.query(a['geometries'][ia],predicate='dwithin',distance=space)
            yield ia[x],ib[y]


def scan(r,grid,progress=lambda *_:None):
    entries=r._plans_for_all_poses(lambda n,t:progress(round(n*.12),t))
    original_rows=r.rules.nominal_check()
    by_ref=defaultdict(list)
    for e in entries:by_ref[e['layer'],e['index']].append(e)
    progress(12,'复用共用路径，建立逐图形极端几何')
    refs=[ref for values in r.rules.nets.values() for ref in values];count=grid.count
    def build(ref):
        local=by_ref.get(ref,());owners=tuple(sorted({q['owner'] for q in local if (q['plan'] is not None or q['orphan']) and grid.radices[q['owner']]>1}))
        values={};render_cache={}
        if not local:
            g=r.model.nodes[r.model.ids[ref]]['poly'];values[()]=dict(geometry=g,pieces=())
        else:
            for digits in product(*(range(grid.radices[i]) for i in owners)):
                cancel_check();states=np.zeros((count,3))
                for i,d in zip(owners,digits):states[i]=grid.locals[i][d]
                f=r.frame(states,diagnostics=False,targets=(ref,),render_cache=render_cache)
                row=f['replacements'][ref[0]][0]
                values[digits]=dict(geometry=row['geometry'],pieces=tuple(_compact(q) for q in f['pieces']))
        bounds=np.array([v['geometry'].bounds for v in values.values()])
        envelope=shapely.box(*bounds[:,:2].min(axis=0),*bounds[:,2:].max(axis=0))
        return ref,dict(owners=owners,values=values,envelope=envelope)
    bundles=dict(parallel_map(build,refs,profile=native_phase_profile(),progress=lambda n,total:progress(12+round(28*n/max(1,total)),f'重构逐图形几何 {n}/{total}')))
    from .width_keepout import routing_violations
    from .width_witness import edge_witness
    from .width_analysis import BLOCKED
    from .width_summary import strongest
    routing_lines={};routing_witnesses={};routing_factors=[]
    binary=r.p.target_nsigma in (0.,math.inf)
    for ref,bundle in bundles.items():
        axes=bundle['owners']
        for digits,value in bundle['values'].items():
            cancel_check();states=np.zeros((count,3));all_digits=[0]*count
            for owner,d in zip(axes,digits):states[owner]=grid.locals[owner][d];all_digits[owner]=d
            replaced={ref[0]:[dict(indices=(ref[1],),geometry=value['geometry'])]}
            rows=routing_violations(r.model,states,r.p,r.settings,replaced,replacements_only=True)
            if not rows:continue
            routing_factors.append(PoseClause(tuple((o,(int(d),)) for o,d in zip(axes,digits))))
            for row in rows:strongest(routing_lines,[(row['layer'],i) for i in row['indices']],BLOCKED,None,binary,routing_witnesses,edge_witness(row,grid.index(all_digits),r.model,states))
    for digit in range(max(grid.radices)):
        states=np.asarray([v[digit] if digit<len(v) else v[0] for v in grid.locals])
        for row in routing_violations(r.model,states,r.p,r.settings):
            if r.model.roles.get(row['layer'])=='固定RDL':continue
            node=r.model.nodes[r.model.ids[row['layer'],row['indices'][0]]];owner=node['owner'];all_digits=[0]*count
            if owner>=0:all_digits[owner]=digit if digit<grid.radices[owner] else 0
            term=PoseClause(((owner,(all_digits[owner],)),) if owner>=0 else ())
            routing_factors.append(term)
            strongest(routing_lines,[(row['layer'],i) for i in row['indices']],BLOCKED,None,binary,routing_witnesses,edge_witness(row,grid.index(all_digits),r.model,states))
    records=defaultdict(set);witnesses={};locations=defaultdict(dict);reasons=defaultdict(set);zone_defs={};scene_records=set();record_lock=threading.Lock()
    for bundle in bundles.values():
        for v in bundle['values'].values():
            for q in v['pieces']:zone_defs.setdefault(zone_key(q),q)
    def record(pieces,rows,axes,digits,all_bad=False):
        all_digits=[0]*count
        for i,d in zip(axes,digits):all_digits[i]=d
        index=grid.index(all_digits);term=PoseClause(tuple((i,(int(d),)) for i,d in zip(axes,digits)))
        if all_bad or any(rows.values()):
            with record_lock:scene_records.add(term)
        for q in pieces:
            key=zone_key(q);issues=rows.get(key,())
            if not issues:continue
            geo=shapely.union_all([v['geometry'] for v in issues if not v['geometry'].is_empty])
            score=(sum(v.get('connection',False) for v in issues),float(geo.area),-index)
            with record_lock:
                records[key].add(term);reasons[key].update(v['kind'] for v in issues)
                if not geo.is_empty:locations[key][geo.wkb]=geo
                if key not in witnesses or score>witnesses[key]['score']:
                    witnesses[key]=dict(index=index,choices=tuple(all_digits),score=score,kinds=tuple(v['kind'] for v in issues),geometry=geo)
    def get(ref,digits_by_owner):
        b=bundles[ref];return b['values'][tuple(digits_by_owner[i] for i in b['owners'])]
    components=_component_groups(r,bundles)
    def local_table(item):
        number,(net,component,vias)=item;calls=0
        axes=tuple(sorted({i for ref in component for i in bundles[ref]['owners']}))
        table={};digits_by_owner=[0]*count
        for digits in product(*(range(grid.radices[i]) for i in axes)):
            cancel_check();states=np.zeros((count,3))
            for i,d in zip(axes,digits):digits_by_owner[i]=d;states[i]=grid.locals[i][d]
            items=[get(ref,digits_by_owner) for ref in component]
            geos=[v['geometry'] for v in items];g=geos[0] if len(geos)==1 else shapely.union_all(geos)
            ps=tuple(q for v in items for q in v['pieces'])
            raw=r.rules.local(net,g,states,[])
            rows=[dict(kind=k,geometry=p,layers=tuple({ref[0] for ref in component}),net=net) for k,p in raw]
            record(ps,associate(ps,[*rows,*original_rows],r.tol),axes,digits,bool(raw))
            table[digits]=(g,ps);calls+=1
        # Enclosure needs the copper owners plus ONE via owner, never every
        # independently moving via on the net in one Cartesian product.
        templates={}
        for g,ps in table.values():
            for q in ps:
                key=zone_key(q)
                if key not in templates:templates[key]=shapely.union_all([r.model.nodes[r.model.ids[tuple(ref)]]['poly'] for ref in q.get('via_refs',())])
        for ref,owner,required in vias:
            scope=tuple(sorted(set(axes)|({owner} if owner>=0 and grid.radices[owner]>1 else set())))
            for digits in product(*(range(grid.radices[o]) for o in scope)):
                cancel_check();z=np.zeros((count,3));d=dict(zip(scope,digits))
                for o,value in d.items():z[o]=grid.locals[o][value]
                g,ps=table[tuple(d[o] for o in axes)]
                missing=r.model.moved(required,owner,z).difference(g)
                if missing.area<=r.tol*r.tol:continue
                placed=tuple(dict(q,moving_terminals=r.model.moved(templates[zone_key(q)],q['die'],z)) for q in ps)
                rows=[dict(kind='孔盘包围不足',geometry=missing,layers=tuple({v[0] for v in component}),net=net)]
                record(placed,associate(placed,rows,r.tol),scope,digits,True)
        envelope=shapely.union_all([bundles[ref]['envelope'] for ref in component]).envelope
        return dict(net=net,owners=axes,values=table,envelope=envelope),calls
    # The native backend serialises its database access. More frontend threads
    # thrash its region cache; two overlap geometry preparation with native work.
    native_profile=resource_profile();native_profile=dict(native_profile,workers=min(2,native_profile['workers']))
    results=parallel_map(local_table,list(enumerate(components)),profile=native_profile,progress=lambda n,total:progress(40+round(20*n/max(1,total)),f'重构局部规则 {n}/{total}'))
    tables=[row[0] for row in results];calls=sum(row[1] for row in results)
    for t in tables:
        items=list(t['values'].items());t['items']=items
        t['digits']=np.asarray([k for k,v in items],int).reshape(len(items),len(t['owners']))
        t['geometries']=np.asarray([v[0] for k,v in items],object);t['tree']=shapely.STRtree(t['geometries'])
    tree=shapely.STRtree([t['envelope'] for t in tables]);pairs=[]
    for i,t in enumerate(tables):
        for j in tree.query(t['envelope'],predicate='dwithin',distance=r.rules.space):
            j=int(j)
            if j>i and t['net'][0]==tables[j]['net'][0] and t['net']!=tables[j]['net']:pairs.append((i,j))
    def check_pair(item):
        n,(i,j)=item;pair_checks=0
        a,b=tables[i],tables[j];axes=tuple(sorted(set(a['owners'])|set(b['owners'])))
        # Query actual posed geometries in bulk before invoking native DRC.
        # The shared-DIE join is mandatory: spatial pairs alone would invent
        # incompatible positions for two traces belonging to the same DIE.
        pairs_for_states=(pair for ia,ib in compatible_candidates(a,b,r.rules.space) for pair in zip(ia,ib))
        for ka,kb in pairs_for_states:
            cancel_check();da,(ga,pa)=a['items'][int(ka)];db,(gb,pb)=b['items'][int(kb)]
            d={**dict(zip(a['owners'],da)),**dict(zip(b['owners'],db))};digits=tuple(d[o] for o in axes);pair_checks+=1
            bad=r.rules.pair(ga,gb)
            # Actual shorts are failures even when no native parallel edge pair exists.
            if ga.intersects(gb):bad=shapely.union_all([bad,ga.intersection(gb).buffer(r.tol)])
            if bad.is_empty:continue
            ps=pa+pb;rows=[dict(kind='异网线距不足 / 接触',geometry=bad,layers=tuple({q['layer'] for q in ps}),net=(a['net'],b['net']))]
            record(ps,associate(ps,rows,r.tol),axes,digits,True)
        return pair_checks
    pair_checks=sum(parallel_map(check_pair,list(enumerate(pairs)),profile=native_profile,progress=lambda n,total:progress(60+round(35*n/max(1,total)),f'重构异网组合 {n}/{total}')))
    clauses=tuple(sorted(scene_records,key=lambda c:c.allowed))
    zones={}
    for key,q in zone_defs.items():
        local=tuple(sorted(records[key],key=lambda c:c.allowed));idx=ProblemIndex(grid,local)
        geos=list(locations.get(key,{}).values());marks=shapely.union_all(geos) if geos else EMPTY
        zones[key]=dict(complete=True,has_failure=bool(local),clauses=local,failing_states=idx.total+idx.zero,
                        kinds=tuple(sorted(reasons[key])),witness=witnesses.get(key),markers=marks,
                        probability=dict(lower=0.,upper=1.,certified=False,reason='有限离散极端不能证明连续概率上界'))
    progress(100,'全部离散重构极端已核验')
    return dict(complete=True,routing_summary=dict(lines=routing_lines,witnesses=routing_witnesses,red_factors=routing_factors),grid_key=tuple(v.tobytes() for v in grid.locals),grid=grid,zones=zones,
                red_factors=clauses,package_states=grid.total+1,geometry_checks=sum(len(b['values']) for b in bundles.values()),
                local_checks=calls,pair_checks=pair_checks,scope='全部离散极端；实际重构宽度、间距、包围量、边距、禁布区与连接。连续概率另行给出界限。')
