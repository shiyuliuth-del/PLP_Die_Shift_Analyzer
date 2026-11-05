"""Evaluate changed copper where independent original-geometry rules do not apply."""
import math
import numpy as np
import shapely
from collections import defaultdict
from .width_keepout import routing_violations,forbidden
from .extreme_filter import ProblemIndex
from .width_summary import strongest,combinations
from .width_analysis import BLOCKED
from .width_witness import edge_witness


def reconstruction_edge_scopes(engine,grid,progress):
    """Prove every possible cut, moved fragment and bridge stays inside the edge rule.

    Cuts are subsets of the original fixed copper. Bridge faces lie in the
    convex hull of their two placements; optional GEOS mitres have limit five.
    An enclosing rectangle may overestimate reach but cannot certify a risky
    copper feature. Nonconvex boards and holes use the actual board polygon.
    """
    from .acceleration import rigid_pose_batch
    recon=engine.reconstructor;owners=defaultdict(set);model=engine.contact
    for via in recon.vias:
        owner=via['owner']
        if owner>=0 and grid.radices[owner]>1:
            for ref in via['targets']:owners[ref].add(owner)
    board=model.case['board'];boundary=board.boundary
    rule=engine.p.line_space_um-recon.settings.drc_tolerance_um
    scopes=defaultdict(list)
    for n,((layer,index),dies) in enumerate(owners.items()):
        if n%32==0:progress(round(100*n/max(1,len(owners))),'核对重构铜整包可达边界')
        x0,y0,x1,y1=model.cache.layers[layer]['geometry'][index].bounds
        corners=np.array([[x0,y0],[x0,y1],[x1,y0],[x1,y1]])
        lo=corners.min(axis=0);hi=corners.max(axis=0)
        scale=float(np.max(abs(corners)))
        for die in sorted(dies):
            pivot=model.bound.centers[die]
            moved=rigid_pose_batch(corners,pivot,grid.locals[die],recon.settings.acceleration)
            lo=np.minimum(lo,moved.min(axis=(0,1)));hi=np.maximum(hi,moved.max(axis=(0,1)))
            scale=max(scale,float(np.max(abs(pivot))),float(np.max(abs(moved))))
        pad=8*recon.width_um+4*recon.tol+64*np.finfo(float).eps*(1+scale)
        envelope=shapely.box(*(lo-pad),*(hi+pad))
        if not board.covers(envelope) or envelope.distance(boundary)<=rule or envelope.intersects(forbidden(model)):
            scopes[tuple(sorted(dies))].append((layer,index))
    return scopes


def scan_reconstruction(engine,grid,settings,base,progress):
    # Original RDL has no edge faults anywhere on the discrete grid. Only
    # rebuilt polygons can introduce one. Rigid poses on other sources are
    # therefore provably irrelevant to each of these boundary checks.
    scopes=reconstruction_edge_scopes(engine,grid,progress)
    sizes={axes:math.prod(grid.radices[i] for i in axes) for axes in scopes}
    if any(n>262144 for n in sizes.values()):return None  # bounded-memory streaming fallback
    lines=base['channel_lines'].copy();witnesses=base['channel_witnesses'].copy();factors=list(base['channel_factors'])
    count=sum(sizes.values());done=0;binary=engine.p.target_nsigma in (0.,math.inf)
    for axes,refs in scopes.items():
        flag=np.zeros(tuple(grid.radices[i] for i in axes),bool)
        for local,z in combinations(grid,axes):
            title=f'重构铜封装边距 {done+1}/{count}'
            def update(*_):progress(round(100*done/count),title)
            update()
            result=engine.reconstructor.frame(z,None,update,targets=refs)
            replacements=result['replacements']
            if replacements:
                edges=routing_violations(engine.contact,z,engine.p,settings,replacements,replacements_only=True)
                flag[local]=bool(edges)
                if edges:
                    digits=[0]*grid.count
                    for owner,d in zip(axes,local):digits[owner]=d
                    for row in edges:
                        ref=[(row['layer'],i) for i in row['indices']]
                        strongest(lines,ref,BLOCKED,None,binary,witnesses,edge_witness(row,grid.index(digits),engine.contact,z))
            done+=1
        if np.any(flag):factors.append((axes,flag))
    progress(100,'重构铜包络与相关芯片姿态已全部核实')
    return dict(lines=lines,witnesses=witnesses,grid_key=base['grid_key'],red_factors=factors,
                complete=True,package_states=grid.total+1,scanned=done,edge_certified=not scopes,
                boundary_sources=sum(map(len,scopes.values())))



def scan(engine,grid,settings,width,focused,progress):
    base=engine.width_summary(progress) if width else None
    if base and engine.deformer is None and getattr(engine,'reconstructor',None) is not None and not base['has_edge_risk']:
        checked=scan_reconstruction(engine,grid,settings,base,progress)
        if checked is not None:return checked
    channels=ProblemIndex(grid,base['channel_factors']) if base else None
    lines=dict(base['channel_lines']) if base else {}
    witnesses=dict(base['channel_witnesses']) if base else {}
    whole=width or focused is None
    visits=range(-1,grid.total) if whole else (-1,*focused)
    count=grid.total+1 if whole else len(visits)
    red=[];zero=np.zeros((grid.count,3));binary=engine.p.target_nsigma in (0.,math.inf)
    for n,index in enumerate(visits):
        title=f'逐姿态核实型变后'+('宽度' if width else '接触')+f' {n+1}/{count}'
        update=lambda *_:progress(round(100*n/count),title)
        update()
        digits=grid.choices(index) if index>=0 else [0]*grid.count
        z=np.asarray([v[k] for v,k in zip(grid.locals,digits)]) if index>=0 else zero
        current=engine.contact.current(z,engine.p) if engine.deformer or not width else None
        deformation=engine.deformer.frame(z,current,update) if engine.deformer else None
        replacements=(deformation or {}).get('replacements',{})
        if not width:
            if deformation is not None and 'blocked_count' in deformation:
                # The deformation engine already checked this final replacement
                # set against every foreign net at this exact pose.
                problem=bool(deformation['blocked_count'])
            else:
                if replacements:current=engine.contact.current(z,engine.p,replacements)
                problem=bool(current['issues'])
        else:
            reconstruction=engine.reconstructor.frame(z,deformation,update) if getattr(engine,'reconstructor',None) else None
            rebuilt=(reconstruction or {}).get('replacements',{})
            edges=routing_violations(engine.contact,z,engine.p,settings,engine.final_copper(replacements,rebuilt))
            problem=bool(edges) or channels.matches(digits)
            for row in edges:
                layer=row['layer'];i=row['indices'][0];node=engine.contact.nodes[engine.contact.ids[layer,i]]
                event=None
                if not (replacements or rebuilt) and engine.width_analyzer.probability and row['kind']!='禁布区侵入':
                    event=engine.width_analyzer.probability.edge(layer,i,node['poly'],node['owner'],row['required_um'])
                strongest(lines,[(layer,j) for j in row['indices']],BLOCKED,event,binary,witnesses,
                          edge_witness(row,index,engine.contact,z))
        if problem and index>=0:red.append(index)
    return dict(lines=lines,witnesses=witnesses,grid_key=tuple(v.tobytes() for v in grid.locals),
                indices=red,complete=True,package_states=grid.total+1,scanned=count)
