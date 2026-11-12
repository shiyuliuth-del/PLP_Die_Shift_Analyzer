"""Joint finite-grid yield of the ACTUAL reconstructed fixed-RDL copper.

Factor by electrical net and interacting net pairs. Shared DIE states stay
shared; no multiplication of marginal via or route pass probabilities.
"""
from itertools import product
import math
import numpy as np
import shapely
from .width_grid import WidthGrid,weighted_pair
from .extreme_filter import ProblemIndex,PoseClause


def independent_components(book,entries,p,style):
    """Split an electrical net only where full-input AABBs prove independence.

    Copper connected through another layer can be far apart on this plane.
    Keeping it in one numerical factor would unnecessarily form a huge DIE
    Cartesian product. Curved biarcs use the unsplit conservative fallback.
    """
    if style=='arc':return book
    from copy import copy
    from collections import defaultdict
    from .statistics import mean_and_bounds
    model=book.model;count=len(model.bound.centers);by_ref=defaultdict(set)
    for q in entries:by_ref[q['layer'],q['index']].add(q['owner'])
    result=copy(book);result.nets={};result.original={};result.vias=defaultdict(list);result.refnet={}
    for key,refs in book.nets.items():
        bounds=[]
        for ref in refs:
            g=model.nodes[model.ids[ref]]['poly'];travel=0.
            for owner in by_ref[ref]:
                mu,b=mean_and_bounds(p.for_die(owner),count)
                radius=np.max(np.linalg.norm(shapely.get_coordinates(g)-model.bound.centers[owner],axis=1))
                travel=max(travel,float(np.linalg.norm(abs(mu[:2])+b[:2])+2*radius*np.sin(min(np.pi,abs(mu[2])+b[2])/2)))
            box=np.array(g.bounds)+np.array([-1,-1,1,1])*(travel+book.dbu*4)
            bounds.append(shapely.box(*box))
        tree=shapely.STRtree(bounds);groups=[];remaining=set(range(len(refs)))
        while remaining:
            group=set();queue=[remaining.pop()]
            while queue:
                i=queue.pop();group.add(i)
                hits=set(map(int,tree.query(bounds[i],predicate='dwithin',distance=book.space))) & remaining
                remaining-=hits;queue.extend(hits)
            groups.append(sorted(group))
        for n,ids in enumerate(groups):
            k=(*key,n);local=[refs[i] for i in ids];g=shapely.union_all([model.nodes[model.ids[r]]['poly'] for r in local])
            result.nets[k]=local;result.original[k]=g
            for r in local:result.refnet[r]=k
            result.vias[k]=[v for v in book.vias[key] if model.nodes[model.ids[v[0]]]['poly'].intersects(g)]
    return result


def measure_reconstruction_yield(reconstructor,p,cfg,progress):
    r=reconstructor;book=r.rules
    entries=r._plans_for_all_poses(lambda n,t:progress(round(n*.15),t))
    book=independent_components(book,entries,p,cfg.reconstruction_style)
    grid=WidthGrid(p,len(r.model.bound.centers),cfg.reconstruction_grid_nodes)
    choices=[tuple(map(int,np.flatnonzero(w))) for w in grid.weights]
    owners={key:set() for key in book.nets}
    for entry in entries:
        if entry['plan'] is not None or entry['orphan']:owners[book.refnet[entry['layer'],entry['index']]].add(entry['owner'])
    owners={k:tuple(sorted(o for o in v if len(choices[o])>1 or np.any(grid.locals[o][choices[o][0]]))) for k,v in owners.items()}
    tables={};clauses=set();checks=0
    def clause(dependencies,digits):return PoseClause(tuple((owner,(int(digit),)) for owner,digit in zip(dependencies,digits)))
    keys=list(book.nets)
    local_total=sum(math.prod(len(choices[o]) for o in owners[k]) for k in keys)
    always_failed=None
    for key in keys:
        deps=owners[key];rows={}
        targets=book.nets[key]
        for digits in product(*(choices[o] for o in deps)):
            states=np.zeros((grid.count,3))
            for owner,digit in zip(deps,digits):states[owner]=grid.locals[owner][digit]
            frame=r.frame(states,targets=targets,diagnostics=False)
            # Only assemble the requested net; other nets need no union here.
            changed={}
            for layer,values in frame['replacements'].items():
                for q in values:
                    for i in q['indices']:changed[layer,i]=q['geometry']
            geo=shapely.union_all([changed.get(ref,r.model.nodes[r.model.ids[ref]]['poly']) for ref in targets])
            bad=not book.passes(key,geo,states,[]);rows[digits]=(geo,bad)
            if bad:clauses.add(clause(deps,digits))
            checks+=1;progress(15+round(50*checks/max(1,local_total)),f'重构良率 · 局部规则 {checks:,}/{local_total:,}')
        tables[key]=rows
        if rows and all(bad for g,bad in rows.values()):
            always_failed=key;break  # proven failure for all states of one factor
    enclosure_checks=0
    if always_failed is None:
        for n,key in enumerate(keys):
            for ref,owner,required in book.vias[key]:
                deps=tuple(sorted(set(owners[key])|({owner} if owner>=0 and (len(choices[owner])>1 or np.any(grid.locals[owner][choices[owner][0]])) else set())))
                indices=[deps.index(o) for o in owners[key]]
                for digits in product(*(choices[o] for o in deps)):
                    geo,bad=tables[key][tuple(digits[i] for i in indices)]
                    if not bad:
                        states=np.zeros((grid.count,3))
                        for o,digit in zip(deps,digits):states[o]=grid.locals[o][digit]
                        missing=book.model.moved(required,owner,states).difference(geo)
                        if missing.area>book.dbu*book.dbu:clauses.add(clause(deps,digits))
                    enclosure_checks+=1
                    if enclosure_checks%64==0:progress(65+round(10*n/max(1,len(keys))),'重构良率 · 独立接口包围量')
    # Every possible grid geometry is represented in the bounds used here.
    # This prefilter cannot remove an interaction within the finite grid.
    if always_failed is not None:keys=list(tables)
    envelopes=[]
    for key in keys:
        bounds=np.array([g.bounds for g,bad in tables[key].values()])
        envelopes.append(shapely.box(*bounds[:,:2].min(axis=0),*bounds[:,2:].max(axis=0)))
    tree=shapely.STRtree(envelopes);pairs=[]
    for i,a in enumerate(envelopes if always_failed is None else ()):
        for j in tree.query(a,predicate='dwithin',distance=book.space):
            if j>i and keys[i][0]==keys[j][0]:pairs.append((keys[i],keys[j]))
    pair_checks=0
    for n,(a,b) in enumerate(pairs):
        deps=tuple(sorted(set(owners[a])|set(owners[b])))
        ia=[deps.index(o) for o in owners[a]];ib=[deps.index(o) for o in owners[b]]
        done=0;total=math.prod(len(choices[o]) for o in deps)
        for digits in product(*(choices[o] for o in deps)):
            ga,ba=tables[a][tuple(digits[i] for i in ia)];gb,bb=tables[b][tuple(digits[i] for i in ib)]
            if not ba and not bb and book.pair_fails(ga,gb):clauses.add(clause(deps,digits))
            done+=1;pair_checks+=1
            if done%32==0:progress(75+round(23*(n+done/total)/max(1,len(pairs))),f'重构良率 · 异网组合 {n+1}/{len(pairs)} · {done:,}/{total:,}')
        progress(75+round(23*(n+1)/max(1,len(pairs))),'重构良率 · 合并关联芯片事件')
    index=ProblemIndex(grid,sorted(clauses,key=lambda c:c.allowed))
    success,failure=weighted_pair(index,grid.weights)
    progress(100,'重构良率完成：完整有限网格；不是连续空间的精确穷尽')
    return dict(method='grid',value=success,failure=failure,grid_nodes=grid.nodes,
                total=grid.combinations,local_checks=checks,pair_checks=pair_checks,enclosure_checks=enclosure_checks,early_failure_net=always_failed,
                scale_nsigma=p.scale_nsigma,mass_error=grid.mass_error,steps=grid.steps,
                scope='全部固定RDL的线宽/线距、相连过孔包围量、封装边距与禁布区；按最终重构几何。',
                limitation='完整有限网格积分估计，连续空间误差需提高节点数复核；不包含未建模的制造缺陷。')
