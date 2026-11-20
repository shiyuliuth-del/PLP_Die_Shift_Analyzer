"""Source-bound direction preference and co-located cuts on adjacent straight nets."""
import numpy as np
import shapely


def alignment(plan):
    edges=np.concatenate([np.diff(shapely.get_coordinates(ring),axis=0) for ring in shapely.get_parts(plan.source.boundary)]);length=np.linalg.norm(edges,axis=1)
    long=length>=max(plan.tolerance*16,float(length.max(initial=0))*.35)
    if not np.any(long):return 1.
    u=plan.tangent((plan.nominal_first+plan.nominal_second)/2)
    return float(np.max(np.abs(edges[long]@u)/length[long]))


def straight_runs(plan,minimum):
    """Collinear route sections, including the long legs of bent traces."""
    limit=np.cos(np.deg2rad(2.))
    start=0
    for stop in range(1,len(plan.lengths)+1):
        axis=plan.tangent(plan.marks[start])
        if stop<len(plan.lengths) and plan.tangent(plan.marks[stop])@axis>=limit:continue
        eps=max(plan.tolerance*16,.01)
        lo=max(plan.land_end,plan.marks[start])+eps
        auto_end=min(plan.fixed_start,getattr(plan,'default_fixed_start',plan.fixed_start))
        hi=min(auto_end,plan.marks[stop])-eps
        for pos,radius in plan.corners:
            if pos==plan.marks[start]:lo=max(lo,pos+radius+eps)
            if pos==plan.marks[stop]:hi=min(hi,pos-radius-eps)
        if hi-lo>=minimum:
            point=np.asarray(plan.route.interpolate(lo).coords[0])
            direction=np.asarray(plan.route.interpolate(hi).coords[0])-point
            direction/=np.linalg.norm(direction)
            yield lo,hi,point,direction
        start=stop


def coordinate(r,entries):
    manual={tuple(row[:2]) for name in ('reconstruction_intervals','reconstruction_angles','reconstruction_added','reconstruction_deleted','reconstruction_cuts','reconstruction_extra_cuts') for row in getattr(r.settings,name,())}
    minimum=max(2*r._rule_width,r.tol*64)
    plans=[]
    for e in entries:
        plan=e.get('plan');ref=(e['layer'],e['index'])
        if plan is None or ref in manual or ref in getattr(r,'box_refs',()) or r.settings.reconstruction_regions or e.get('graph_branch'):continue
        runs=tuple(straight_runs(plan,minimum))
        if runs:plans.append((e,plan,runs))
    if len(plans)<2:return entries
    geos=[q[1].source for q in plans];tree=shapely.STRtree(geos);parents=list(range(len(plans)))
    groups={i:{i:None} for i in parents};selected={}
    def root(i):
        while parents[i]!=i:i=parents[i]
        return i
    def projected(run,axis):
        lo,hi,point,direction=run;scale=float(direction@axis);start=float(point@axis)
        bounds=sorted((start,start+(hi-lo)*scale))
        return bounds,start,scale
    for i,(e,p,runs) in enumerate(plans):
        for j in tree.query(p.source,predicate='dwithin',distance=max(r.rule_space*4,r._rule_width*4)):
            j=int(j);other,q,other_runs=plans[j]
            if j<=i or e['owner']!=other['owner'] or e['layer']!=other['layer'] or e['index']==other['index']:continue
            a,b=root(i),root(j)
            if a==b:continue
            options=[]
            for first in (groups[a][i],) if groups[a][i] is not None else runs:
                axis=first[-1]
                for second in (groups[b][j],) if groups[b][j] is not None else other_runs:
                    members={**groups[a],**groups[b],i:first,j:second}
                    if any(abs(run[-1]@axis)<.999 for run in members.values()):continue
                    # Nearby material must be on these runs, not just near a
                    # different leg of either source at a remote corner.
                    u=shapely.Point(first[2]);v=shapely.Point(second[2])
                    transverse=abs(float((np.asarray(v.coords[0])-np.asarray(u.coords[0]))@[-axis[1],axis[0]]))
                    if transverse>max(r.rule_space*6,r._rule_width*6):continue
                    ranges={k:projected(run,axis) for k,run in members.items()}
                    lo=max(value[0][0] for value in ranges.values());hi=min(value[0][1] for value in ranges.values())
                    if hi-lo<minimum:continue
                    options.append((hi-lo,lo,hi,members,ranges))
                    preferred=[]
                    for k,run in members.items():
                        plan=plans[k][1];_,start,scale=ranges[k];origin=run[0]
                        preferred.append(sorted((start+(plan.nominal_first-origin)*scale,start+(plan.nominal_second-origin)*scale)))
                    # The available axial span can include a pad flare. The
                    # intersection of already chosen equal-width spans is a
                    # direct co-located candidate before searching shorter ones.
                    a0=max(lo,max(b[0] for b in preferred));b0=min(hi,min(b[1] for b in preferred))
                    if b0-a0>=minimum:options.append((b0-a0,a0,b0,members,ranges))
                    for cut in (.03,.07,.12,.2):
                        low=lo+(hi-lo)*cut;high=hi-(hi-lo)*cut
                        if high-low>=minimum:options.append((high-low,low,high,members,ranges))
            for _,lo,hi,members,ranges in sorted(options,key=lambda x:-x[0]):
                candidates=[]
                for k,((_,_),start,scale) in ranges.items():
                    plan=plans[k][1];origin=members[k][0]
                    left,right=sorted((origin+(lo-start)/scale,origin+(hi-start)/scale))
                    # Never trade a long robust transition for a tiny shared cut.
                    if right-left<.5*(plan.nominal_second-plan.nominal_first):break
                    part=plan.partition(left,right)
                    if part is None or plan.width_variation(part['a'],part['b'])>.05:break
                    candidates.append((plan,part['a'],part['b']))
                else:
                    parents[b]=a;groups[a]=members
                    for plan,left,right in candidates:selected[id(plan)]=(left,right)
                    break
    if not selected:return entries
    from copy import copy
    result=[]
    for entry in entries:
        plan=entry['plan']
        if id(plan) not in selected:result.append(entry);continue
        updated=copy(plan);updated.nominal_first,updated.nominal_second=selected[id(plan)]
        result.append(dict(entry,plan=updated))
    return result
