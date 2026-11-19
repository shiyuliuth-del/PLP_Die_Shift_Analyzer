"""Exact finite-grid fallback when electrical repairs couple different nets.

Independent raw shapes are computed once per local pose. Joint scenes reuse
them, apply the same electrical guard as the canvas, and check final copper.
No marginal probabilities are multiplied across shared DIE events.
"""
from collections import defaultdict
from itertools import product
import math
import numpy as np
import shapely
from .resources import cancel_check,parallel_map
from .extreme_filter import PoseClause,ProblemIndex


def entry_owners(entries,grid):
    owners=set()
    for q in entries:
        if q.get('following'):owners.update(q.get('follow_owners',(q['owner'],)))
        elif q['plan'] is not None or q['orphan']:owners.add(q['owner'])
    return tuple(sorted(o for o in owners if o>=0 and grid.radices[o]>1))


def prepare(r,grid,progress=lambda *_:None,*,indices=None):
    from .evaluation_scope import digits_for
    selected=None if indices is None else tuple(tuple(digits_for(grid,i)) for i in indices)
    signature=(tuple(v.tobytes() for v in grid.locals),selected)
    projections={}

    cached=getattr(r,'_joint_catalog',None)
    if cached is not None and cached[0]==signature:return cached[1]
    entries=r._plans_for_all_poses(progress);by_ref=defaultdict(list)
    for q in entries:by_ref[q['layer'],q['index']].append(q)
    refs=[ref for rows in r.rules.nets.values() for ref in rows]
    ordered_refs=refs.copy()
    immutable=[ref for ref in refs if ref not in by_ref]
    refs=sorted(by_ref,key=lambda ref:-len(by_ref[ref]))
    if selected is not None:
        for local in by_ref.values():
            owners=entry_owners(local, grid)
            if owners not in projections:
                projections[owners]=sorted({tuple(d[o] for o in owners) for d in selected})
    def static(ref):
        g=r.model.nodes[r.model.ids[ref]]['poly']
        return dict(owners=(),values={():dict(geometry=g,pieces=())},envelope=shapely.box(*g.bounds))
    def build(ref):
        local=by_ref[ref];owners=entry_owners(local,grid);values={};render_cache={}
        from .reconstruction_events import _compact
        choices=projections[owners] if selected is not None else product(*(range(grid.radices[o]) for o in owners))
        for digits in choices:
            cancel_check();states=np.zeros((grid.count,3))
            for owner,digit in zip(owners,digits):states[owner]=grid.locals[owner][digit]
            f=r.frame(states,targets=(ref,),diagnostics=False,render_cache=render_cache,_raw=True)
            row=next(q for q in f['replacements'][ref[0]] if ref[1] in q['indices'])
            values[digits]=dict(geometry=row['geometry'],pieces=tuple(_compact(q) for q in f['pieces'] if (q['layer'],q['index'])==ref))
        bounds=np.array([v['geometry'].bounds for v in values.values()])
        return ref,dict(owners=owners,values=values,envelope=shapely.box(*bounds[:,:2].min(axis=0),*bounds[:,2:].max(axis=0)))
    bank={ref:static(ref) for ref in immutable}
    bank.update(parallel_map(build,refs,progress=lambda n,total:progress(round(100*n/max(1,total)),f'复用局部重构几何 {n}/{total}')))
    bank={ref:bank[ref] for ref in ordered_refs}
    r._joint_catalog=(signature,bank);return bank


def needed(r,bundles,grid=None):
    from .reconstruction_events import compatible_candidates
    mode=r.settings.reconstruction_fixed_via_mode
    if mode!='off':
        from .ignored_motion import linked_fixed_vias
        ports=linked_fixed_vias(r,r._plans or ())
        if ports and (mode=='global' or ports.keys()&set(r.settings.reconstruction_fixed_via_follows)):return True
    tables=[]
    for ref,b in bundles.items():
        if any(q.get('connection_failed') for v in b['values'].values() for q in v['pieces']):return True
        if grid is not None:
            source=r.model.nodes[r.model.ids[ref]]['poly']
            from .topology_routing import covers_ports
            for via,owner,_ in r.rules.vias[r.rules.refnet[ref]]:
                physical=r.model.nodes[r.model.ids[via]]['poly']
                if source.intersection(physical).area<=r.tol*r.tol:continue
                for ds,v in b['values'].items():
                    states=np.zeros((grid.count,3));digits=dict(zip(b['owners'],ds))
                    positions=(grid.locals[owner][digits[owner]],) if owner in digits else grid.locals[owner] if owner>=0 else (np.zeros(3),)
                    for pose in positions:
                        if owner>=0:states[owner]=pose
                        if not covers_ports(v['geometry'],[r.model.moved(physical,owner,states)],r.tol):return True
        items=[(digits,(v['geometry'],v['pieces'])) for digits,v in b['values'].items()]
        geos=np.asarray([v[0] for _,v in items],object)
        tables.append(dict(ref=ref,kind='copper',net=r.rules.refnet[ref],owners=b['owners'],items=items,
                           digits=np.asarray([k for k,_ in items],int).reshape(len(items),len(b['owners'])),geometries=geos,tree=shapely.STRtree(geos),envelope=b['envelope']))
    if grid is not None:
        virtual={}
        for key,ports in r.rules.vias.items():
            for ref,owner,_ in ports:virtual[('port',key[0],ref)]=(key,ref,owner)
        for node in r.model.nodes:
            if r.model.roles[node['key']]!='固定RDL':virtual['metal',node['key'].split('/')[0],(node['key'],node['index'])]=((node['key'].split('/')[0],node['net']),(node['key'],node['index']),node['owner'])
        for (kind,plane,ref),(net,_,owner) in virtual.items():
            original=r.model.nodes[r.model.ids[ref]]['poly'];owners=(owner,) if owner>=0 and grid.radices[owner]>1 else ();values=[];digits=[]
            for digit in range(grid.radices[owner] if owners else 1):
                states=np.zeros((grid.count,3))
                if owner>=0:states[owner]=grid.locals[owner][digit]
                values.append(r.model.moved(original,owner,states));digits.append((digit,) if owners else ())
            bounds=np.array([g.bounds for g in values]);geos=np.asarray(values,object)
            tables.append(dict(kind=kind,net=net,owners=owners,digits=np.asarray(digits,int).reshape(len(values),len(owners)),geometries=geos,
                               tree=shapely.STRtree(geos),envelope=shapely.box(*bounds[:,:2].min(axis=0),*bounds[:,2:].max(axis=0))))
    tree=shapely.STRtree([t['envelope'] for t in tables])
    for i,a in enumerate(tables):
        for j in tree.query(a['envelope'],predicate='intersects'):
            b=tables[int(j)]
            if j<=i or a['net'][0]!=b['net'][0] or a['net']==b['net']:continue
            if a['kind']==b['kind']=='port':continue
            if any(len(x) for x,y in compatible_candidates(a,b,0.)):return True
    return False


def scene(r,grid,bundles,digits,diagnostics=True):
    from .topology_routing import resolve
    from .reconstruction import decorate_problems
    states=np.array([poses[d] for poses,d in zip(grid.locals,digits)]);out={};pieces=[]
    for ref,b in bundles.items():
        row=b['values'][tuple(digits[o] for o in b['owners'])]
        out.setdefault(ref[0],[]).append(dict(indices=(ref[1],),geometry=row['geometry'],net=r.rules.refnet[ref][1]))
        pieces.extend(dict(q,regions=dict(q['regions'])) for q in row['pieces'])
    out,pieces,repaired,blocked=resolve(r,out,pieces,states)
    if blocked:
        # The interactive path can also try its bounded quick angle candidate.
        # Statistics must use exactly the same completed copper in that case.
        alternate=r.frame(states,diagnostics=False)
        if diagnostics:
            alternate=dict(alternate,violations=r.rules.check(alternate['replacements'],states),original_violations=r.rules.nominal_check(),constraints_complete=True)
            decorate_problems(alternate)
        return states,alternate
    from .ignored_motion import add_linked_geometry
    out=add_linked_geometry(r,out,states)
    frame=dict(replacements=out,pieces=pieces,violations=r.rules.check(out,states) if diagnostics else [],original_violations=r.rules.nominal_check() if diagnostics else (),dbu_um=r.tol,
               repair_refs=repaired,hard_failures=blocked,electrically_valid=not bool(blocked))
    decorate_problems(frame);return states,frame


def decisions(r):
    from .session_cache import EphemeralCache
    bank=getattr(r,'_joint_decisions',None)
    if bank is None:r._joint_decisions=bank=EphemeralCache(budget=2*1024**2,max_entries=8192)
    return bank


def remember(r,states,frame):
    from .reconstruction_events import scene_failure
    value=(scene_failure(frame),bool(frame['repair_refs']))
    decisions(r).put(states.tobytes(),value,size=states.nbytes+128)
    return value


def scan(r,grid,bundles,progress=lambda *_:None,indices=None):
    from .reconstruction_events import zone_key,active_region,current_failures,zone_severity,RANK
    from .width_keepout import routing_violations
    from .width_summary import strongest
    from .width_witness import edge_witness
    from .width_analysis import BLOCKED
    records=defaultdict(set);reasons=defaultdict(set);severity={};witnesses={};zones_seen=set();scene_records=set();connection_records=set()
    routing_lines={};routing_witnesses={};routing_factors=[];binary=r.p.target_nsigma in (0.,math.inf);repairs=0
    from .evaluation_scope import digits_for
    visits=(digits_for(grid,i) for i in indices) if indices is not None else product(*(range(n) for n in grid.radices))
    total=len(indices) if indices is not None else grid.total+1
    # Each lane owns its mutable plan, rule and frame caches. Reduction stays in
    # catalogue order; no material edits or Qt objects are shared between poses.
    import threading
    from contextlib import ExitStack
    from .resources import parallel_iter, resource_profile, geometry_budget
    from .reconstruction import Reconstructor
    from .reconstruction_status import clone_plan_pool
    profile=resource_profile()
    workers=min(total,geometry_budget(profile,sum(n['poly'].__sizeof__()+shapely.get_num_coordinates(n['poly'])*32 for n in r.model.nodes)))
    local=threading.local();lock=threading.Lock();solvers=[]
    if workers>1:r._plans_for_all_poses(progress)
    def evaluate(digits):
        cancel_check();solver=r
        if workers>1:
            solver=getattr(local,'solver',None)
            if solver is None:
                solver=Reconstructor(r.model,r.p,r.settings,plan_pool=clone_plan_pool(r.plan_pool),geometry_pool=r.geometry_pool,reference=r)
                solver._plans=clone_plan_pool({'current':r._plans})['current']
                solver.auto_following=set(r.auto_following);solver.auto_ignored=dict(r.auto_ignored);solver.safe_vias=dict(r.safe_vias)
                solver._envelopes=getattr(r,'_envelopes',{})
                if getattr(r,'measured_only',False):solver.measured_only=True
                local.solver=solver
                with lock:solvers.append(solver)
        if bundles is not None:states,frame=scene(solver,grid,bundles,digits)
        else:
            states=np.asarray([v[d] for v,d in zip(grid.locals,digits)])
            frame=solver.frame(states)
        return digits,states,frame
    with ExitStack() as cleanup:
      cleanup.callback(lambda:[s.close() for s in solvers])
      values=parallel_iter(evaluate,visits,dict(profile,workers=workers));cleanup.callback(values.close)
      for number,(digits,states,frame) in enumerate(values):
        cancel_check()
        repairs+=bool(frame['repair_refs'])
        remember(r,states,frame)
        clause=PoseClause(tuple((o,(int(d),)) for o,d in enumerate(digits)));index=grid.index(digits)
        if frame['violations'] or frame['hard_failures'] or current_failures(frame):scene_records.add(clause)
        if frame['hard_failures'] or any(q.get('connection_failed') for q in frame['pieces']):connection_records.add(clause)
        for q in frame['pieces']:
            key=zone_key(q);zones_seen.add(key);rows=q.get('rule_witnesses',())
            if not rows and not q.get('connection_failed'):continue
            records[key].add(clause);reasons[key].update(q.get('problem_reasons',()))
            rank=zone_severity(q,rows)
            if RANK[rank]>RANK[severity.get(key,'green')]:severity[key]=rank
            g=max((v['geometry'] for v in rows if v.get('phase')!='original'),key=lambda g:g.area,default=active_region(q))
            score=(RANK[rank],g.area,-index)
            if key not in witnesses or score>witnesses[key]['score']:witnesses[key]=dict(index=index,choices=tuple(digits),score=score,kinds=q.get('problem_reasons',()),geometry=g)
        edges=routing_violations(r.model,states,r.p,r.settings,frame['replacements'])
        if edges:routing_factors.append(clause)
        for row in edges:strongest(routing_lines,[(row['layer'],i) for i in row['indices']],BLOCKED,None,binary,routing_witnesses,edge_witness(row,index,r.model,states))
        progress(round(100*(number+1)/total),f'电连接补偿后联合核验 {number+1:,}/{total:,}')
    zones={}
    for key in zones_seen:
        clauses=tuple(sorted(records[key],key=lambda q:q.allowed));idx=ProblemIndex(grid,clauses)
        zones[key]=dict(complete=True,has_failure=bool(clauses),clauses=clauses,failing_states=idx.total+idx.zero,kinds=tuple(sorted(reasons[key])),
                        witness=witnesses.get(key),severity=severity.get(key,'green'),
                        probability=dict(lower=0.,upper=1.,certified=False,reason='有限离散极端不能证明连续概率上界'))
    return dict(complete=True,grid=grid,grid_key=tuple(v.tobytes() for v in grid.locals),zones=zones,
                routing_summary=dict(lines=routing_lines,witnesses=routing_witnesses,red_factors=routing_factors),
                connection_factors=tuple(sorted(connection_records,key=lambda q:q.allowed)),red_factors=tuple(sorted(scene_records,key=lambda q:q.allowed)),package_states=total,selected_indices=tuple(indices) if indices is not None else None,
                geometry_checks=sum(len(b['values']) for b in bundles.values()) if bundles is not None else total,local_checks=total,pair_checks=total,
                joint_repaired_states=repairs,scope='全部离散极端；按电连接补偿后的实际铜联合核验。连续概率另给界限。')


def yield_value(r,p,cfg,grid,bundles,progress,partial=None):
    from .width_grid import weighted_pair
    clauses=[];count=0;repaired=0;choices=[tuple(map(int,np.flatnonzero(w))) for w in grid.weights]
    for digits in product(*choices):
        cancel_check();states=np.asarray([v[d] for v,d in zip(grid.locals,digits)])
        hit=decisions(r).get(states.tobytes())
        if hit is None:
            states,frame=scene(r,grid,bundles,digits);hit=remember(r,states,frame)
        bad,repair=hit;count+=1;repaired+=repair
        if bad:clauses.append(PoseClause(tuple((o,(d,)) for o,d in enumerate(digits))))
        if count%16==0 or count==grid.combinations:
            progress(round(100*count/grid.combinations),f'补偿后联合良率 {count:,}/{grid.combinations:,}')
    success,failure=weighted_pair(ProblemIndex(grid,clauses),grid.weights)
    return dict(method='grid',value=success,failure=failure,grid_nodes=grid.nodes,total=grid.combinations,local_checks=count,pair_checks=count,
                enclosure_checks=count,reused_local_checks=sum(len(b['values']) for b in bundles.values()),joint_repaired_states=repaired,
                scale_nsigma=p.scale_nsigma,mass_error=grid.mass_error,steps=grid.steps,
                scope='按电连接补偿后的完整固定RDL几何联合积分，含线宽、线距、包围量、边距和禁布区。',
                limitation='完整有限网格，不是连续空间的穷尽或生产实测良率。')
