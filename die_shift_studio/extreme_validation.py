"""Green is reserved for completely checked states, never absence of markers."""

def frame_status(frame):
    checks=frame.get('checks',{})
    failures=sum(len(q.get('failures',[])) for q in checks.values())
    contact=len(frame.get('contact',{}).get('issues',[]))
    drc=frame.get('drc')
    deformation=frame.get('deformation',{})
    pending=len(deformation.get('diagnostics',[]))
    width=frame.get('width_analysis',{});counts=width.get('counts',{})
    width_failures=counts.get('宽度不足',0)+len(width.get('edge_violations',[]))
    width_pending=counts.get('需局部收窄',0) or (bool(width) and (not width.get('complete',True) or not width.get('examined') or any(r.get('unresolved') or r.get('split') for r in width.get('rows',[]))))
    if failures or contact or pending or drc or width_failures:
        return dict(safe=False,contact=contact,failures=failures,drc=len(drc or []),unadapted=pending,checked=drc is not None,width_failures=width_failures)
    if width_pending:return dict(safe=None,contact=0,failures=0,drc=0,unadapted=0,checked=False,width_pending=True)
    return dict(safe=True if drc is not None and len(checks)==3 else None,contact=0,failures=0,drc=0,unadapted=0,checked=drc is not None)


def evaluate_states(engine,catalog,settings,progress=lambda *_:None):
    """Validate the original layout, storing unary and pairwise factors only.

    A diagonal batch gives each die its k-th local pose. Interface, enclosure,
    edge, keepout and board predicates have a single owner. Die-to-die spacing
    is the only two-owner rule in this undeformed assessment.
    """
    import numpy as np
    from .fast_preview import check_frame
    from .resources import parallel_map
    from .statistics import ExtremeCatalog
    grid=catalog if isinstance(catalog,ExtremeCatalog) else catalog.full
    engine.configure_deformation(False,settings);engine.configure_drc(True,settings)
    n=grid.count;model=engine.contact;bound=engine.bound;p=engine.p
    model.configure_parameters(p);tol=max(1e-9,model.data.dbu_um*1e-6)
    waves=max(grid.radices);unknown=sum(np.count_nonzero(own<0) for k,own in bound.owners.items() if engine.roles.get(k) in ('运动过孔','运动RDL'))
    obstacles='禁布区' in engine.roles.values()
    def wave(k):
        z=np.array([v[k] if k<len(v) else v[0] for v in grid.locals])
        owners={i for i,r in enumerate(grid.radices) if k<r} if k else None
        counts=np.zeros((n+1,3),int)  # contacts, scheme failures, DRC markers; last row is static
        counts[:n,0]=model.current(z,p,counts_only=True,owners=owners)
        layers=bound.transform(z,workers=1) if obstacles else {}
        checks=check_frame(bound,engine.roles,p,None,z,layers,1,model.connections,False,include_pairs=False,active_owners=owners)
        for q in checks['failures']:counts[max(-1,q['owner']),1]+=3
        for q in engine.global_drc.frame(z,settings=settings,owners=owners):
            owner=q.get('owner',-1)
            if q['kind']=='封装边缘线距不足':
                owner=max(-1,int(bound.owners[q['layer']][q['indices'][0]]));counts[owner,1]+=3
            counts[owner,2]+=1
        counts[:,1]+=counts[:,0]
        return counts
    # Warm static trees, template arrays and immutable fixed-RDL rules before
    # sharing them. Later workers build private state arrays, never draw Qt.
    progress(93,'校验名义姿态与固定 RDL');nominal=wave(0)
    rest=parallel_map(wave,range(1,waves),progress=lambda done,total:progress(93+round(4*done/max(1,total)),f'独立芯片姿态批次 {done}/{total}'))
    stacked=np.stack([nominal,*rest])
    local=[stacked[:r,i,:].tolist() for i,r in enumerate(grid.radices)]
    # Exact distance on the full local grid; the envelope test only discards
    # pairs whose bounding boxes are disjoint by MORE than the minimum gap.
    from .representatives import pair_tables
    pairs=pair_tables(bound.case,p,grid,tol)
    pair_checks=sum(grid.radices[q['owners'][0]]*grid.radices[q['owners'][1]] for q in pairs)
    progress(99,'整包离散组合的独立规则校验完成')
    return dict(checked=True,base=nominal[-1].tolist(),local=local,pairs=pairs,unknown=int(unknown),
                local_states=sum(grid.radices),batches=waves,pair_states=pair_checks,package_states=grid.total+1)


def status_for(factors,digits):
    if not factors or not factors.get('checked'):
        return dict(safe=None,checked=False,reason=(factors or {}).get('reason','尚未校验'))
    contact,failures,drc=factors['base']
    for i,digit in enumerate(digits):
        c,f,r=factors['local'][i][digit]
        contact+=c;failures+=f;drc+=r
    for pair in factors['pairs']:
        i,j=pair['owners']
        failures+=3*int(pair['hits'][digits[i]][digits[j]])
    if any(digits):failures+=3*factors['unknown']
    return dict(safe=not (failures or drc or contact),checked=True,contact=contact,failures=failures,drc=drc,unadapted=0,width_failures=0)
