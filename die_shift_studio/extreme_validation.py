"""Green is reserved for completely checked states, never absence of markers."""

def frame_status(frame):
    checks=frame.get('checks',{})
    failures=sum(len(q.get('failures',[])) for q in checks.values())
    contact=len(frame.get('contact',{}).get('issues',[]))
    drc=frame.get('drc')
    deformation=frame.get('deformation',{})
    pending=len(deformation.get('diagnostics',[]))
    if failures or contact or pending or drc:
        return dict(safe=False,contact=contact,failures=failures,drc=len(drc or []),unadapted=pending,checked=drc is not None)
    return dict(safe=True if drc is not None and len(checks)==3 else None,contact=0,failures=0,drc=0,unadapted=0,checked=drc is not None)


def evaluate_states(engine,catalog,settings,progress=lambda *_:None):
    engine.configure_deformation(False,settings);engine.configure_drc(True,settings)
    out=[]
    for i,item in enumerate(catalog):
        progress(93+round(6*i/max(1,catalog.total)),f'极端状态接触与失效检查 {i+1}/{catalog.total}')
        frame=engine.frame(item['states'])
        out.append(frame_status(frame))
    return out
