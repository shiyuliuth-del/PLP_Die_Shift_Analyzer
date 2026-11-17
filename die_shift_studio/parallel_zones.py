"""Source-bound direction preference and co-located cuts on adjacent straight nets."""
import numpy as np
import shapely


def alignment(plan):
    edges=np.concatenate([np.diff(shapely.get_coordinates(ring),axis=0) for ring in shapely.get_parts(plan.source.boundary)]);length=np.linalg.norm(edges,axis=1)
    long=length>=max(plan.tolerance*16,float(length.max(initial=0))*.35)
    if not np.any(long):return 1.
    u=plan.tangent((plan.nominal_first+plan.nominal_second)/2)
    return float(np.max(np.abs(edges[long]@u)/length[long]))


def coordinate(r,entries):
    manual={tuple(row[:2]) for name in ('reconstruction_intervals','reconstruction_angles','reconstruction_added','reconstruction_deleted','reconstruction_cuts','reconstruction_extra_cuts') for row in getattr(r.settings,name,())}
    plans=[]
    for e in entries:
        plan=e.get('plan')
        if plan is None or (e['layer'],e['index']) in manual or r.settings.reconstruction_regions or getattr(r.settings,'reconstruction_boxes',()):continue
        if len(plan.xy)!=2:continue
        u=plan.tangent(0);u=u if u[0]>1e-6 or (abs(u[0])<=1e-6 and u[1]>0) else -u
        if abs(plan.tangent(0)@u)<.999:continue
        plans.append((e,plan,u))
    if len(plans)<2:return
    geos=[q[1].source for q in plans];tree=shapely.STRtree(geos);parents=list(range(len(plans)));groups={i:[i] for i in parents}
    def root(i):
        while parents[i]!=i:i=parents[i]
        return i
    def interval(ids,axis):
        ranges=[]
        for i in ids:
            plan=plans[i][1];start=float(plan.xy[0]@axis);sign=float(plan.tangent(0)@axis)
            eps=max(plan.tolerance*16,.01);lo,hi=sorted((start+sign*(plan.land_end+eps),start+sign*(plan.fixed_start-eps)))
            ranges.append((lo,hi))
        return max(a for a,b in ranges),min(b for a,b in ranges)
    for i,(e,p,axis) in enumerate(plans):
        for j in tree.query(p.source,predicate='dwithin',distance=max(r.rule_space*2,r._rule_width)):
            j=int(j);other,q,v=plans[j]
            if j<=i or e['owner']!=other['owner'] or e['layer']!=other['layer'] or e['index']==other['index'] or abs(axis@v)<.999:continue
            a,b=root(i),root(j)
            if a==b:continue
            ids=groups[a]+groups[b];lo,hi=interval(ids,axis)
            if hi-lo<max(2*r._rule_width,r.tol*64):continue
            candidates=[]
            for k in ids:
                plan=plans[k][1];s0=float(plan.xy[0]@axis);direction=float(plan.tangent(0)@axis)
                left,right=sorted(((lo-s0)/direction,(hi-s0)/direction));part=plan.partition(left,right)
                if part is None or plan.width_variation(left,right)>.05:break
                candidates.append((plan,left,right))
            else:
                parents[b]=a;groups[a]=ids
                for plan,left,right in candidates:plan.nominal_first=left;plan.nominal_second=right
