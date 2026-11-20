"""Cut a connected copper plane when a single separating trace cut is impossible.

Terminal ownership is resolved by branch_regions after all source cuts have
been combined. No nearest-DIE or fabricated cross-hole conductor is introduced.
"""
import math
import numpy as np
import shapely
from .reconstruction_route import RoutePlan,EMPTY


class PlaneRoute(RoutePlan):
    plane_domain=True
    def half(self,s,angle):
        point=np.asarray(self.route.interpolate(s).coords[0]);n=self.tangent(s)
        co,si=np.cos(angle),np.sin(angle);n=np.array([co*n[0]-si*n[1],si*n[0]+co*n[1]])
        u=np.array([-n[1],n[0]]);size=self.source.length+self.length+1
        return self.source.intersection(shapely.Polygon([point-u*size,point+u*size,point+u*size-n*size*2,point-u*size-n*size*2]))
    def edit_pose(self,a,b,which,position,center,state,style,cursor=None):
        part,clamped=self.edit(a,b,which,position,self.pad_hit(cursor,which))
        if part is not None and style=='linear':
            from .reconstruction import transform
            direction=np.asarray(part['far'].centroid.coords[0])-np.asarray(transform(part['near'],center,state).centroid.coords[0])
            if direction@self.tangent((part['a']+part['b'])/2)<=self.tolerance:return self.partition(a,b),True
        return part,clamped

    def partition(self,a,b,angles=(0.,0.)):
        b=self.fixed_side(b)
        if not 0<=a<b<=self.length:return None
        c=EMPTY if a==0 else self.half(a,angles[0])
        fixed=self.source.difference(self.half(b,angles[1]))
        overlap=c.intersection(fixed)
        if overlap.area>self.tolerance**2:
            # Intersecting infinite cut planes at a bend cannot turn their
            # remote overlap into a spurious active copper island.
            return RoutePlan.partition(self,a,b,angles)
        c=c.difference(overlap);fixed=fixed.difference(overlap)
        active=self.source.difference(shapely.union_all([c,fixed]))
        if active.is_empty:return None
        near=shapely.Point(self.route.coords[0]) if a==0 else active.boundary.intersection(c.boundary)
        far=active.boundary.intersection(fixed.boundary)
        if near.is_empty or far.is_empty:return None
        return dict(A=active,B=fixed,C=c,near=near,far=far,a=a,b=b,angles=tuple(angles))


def candidate(base,lines,r):
    """A bounded plane cut candidate; actual final copper is still native-checked."""
    from .reconstruction_route import terminal_marks
    for route in lines:
        plan=PlaneRoute(base['layer'],base['index'],base['owner'],base['refs'],base['routing_source'],route,base['land'],tuple(base['anchors']),r.tol,r.settings.reconstruction_corner_guard_um)
        plan.pad_trigger=r.settings.reconstruction_pad_trigger_um
        fixed=tuple(v['poly'] for v in r.stays if v['net']==base['net'] and plan.source.intersects(v['poly']))
        plan.terminal_vias=(tuple(v['poly'] for v in base['mine']),fixed)
        physical=tuple(any(g.buffer(r.p.annulus_um).covers(shapely.Point(route.coords[0 if side==0 else -1])) for g in values) for side,values in enumerate(plan.terminal_vias))
        plan.pad_modes=(physical[0],False)
        for side,values in enumerate(plan.terminal_vias):
            if not values or not physical[side]:continue
            endpoint=shapely.Point(route.coords[-1 if side else 0]);pad=min(values,key=endpoint.distance)
            marks=terminal_marks(route,pad.buffer(r.p.annulus_um))
            if len(marks):
                if side:plan.fixed_start=max(plan.length*.5,float(min(marks)))
                else:plan.land_end=min(plan.length*.5,float(max(marks)))
        eps=max(r.tol*32,.01);a=min(max(eps,plan.land_end+eps),plan.length*.3);b=max(min(plan.length-eps,plan.fixed_start-eps),plan.length*.7)
        for left,right in ((a,b),(eps,plan.length-eps)):
            part=plan.partition(left,right)
            if part is not None:
                plan.nominal_first=left;plan.nominal_second=right
                return dict(base,plan=plan,orphan=False,graph_branch=True,moving_nominal=base['land'],fixed_nominal=shapely.union_all(base['anchors']),warning='二维铜面适配域：按全部端口约束重构，实际规则独立检查')
    return None


def live_part(r,plan,part,center,pose):
    if part.get('strict_box') is not None:return part
    mode=r.settings.reconstruction_boundary_mode
    if mode=='perpendicular':return part
    from .reconstruction import transform
    a=np.asarray(transform(part['near'],center,pose).centroid.coords[0]);b=np.asarray(part['far'].centroid.coords[0]);direction=b-a
    heading=math.atan2(direction[1],direction[0]);angles=[]
    span=max(0.,float(part['b']-part['a']))
    near_len=part['near'].length if part['near'].geom_type=='LineString' else 0.
    far_len=part['far'].length if part['far'].geom_type=='LineString' else 0.
    blade_height=max(near_len,far_len,1e-6)
    # Prevent wide-plane blades from swinging across distant corners or crossing
    # adjacent cuts: cap tilt so half-blade lateral sweep stays within 30% of span.
    max_tilt=min(math.pi/3,math.atan2(.30*span,max(blade_height*.5,1e-6)))
    if max_tilt<math.radians(1.):return part
    for i,s in enumerate((part['a'],part['b'])):
        tangent=plan.tangent(s);delta=(heading-math.atan2(tangent[1],tangent[0])+math.pi)%(2*math.pi)-math.pi
        angles.append(float(np.clip(delta*(.5 if mode=='balanced' else 1.),-max_tilt,max_tilt)))
    for i,locked in enumerate(part.get('manual_angles',(False,False))):
        if locked:angles[i]=part['angles'][i]
    tilted=plan.partition(part['a'],part['b'],angles)
    if tilted is None:return part
    # Reject tilted blades that touch, cross, or sweep into a much wider neck.
    if tilted['near'].geom_type=='LineString' and tilted['far'].geom_type=='LineString':
        if tilted['near'].distance(tilted['far'])<max(plan.tolerance*8,.35*min(span,part['near'].distance(part['far']) if part['near'].geom_type=='LineString' and part['far'].geom_type=='LineString' else span)):
            return part
        if max(tilted['near'].length,tilted['far'].length)>blade_height*1.35+plan.tolerance*8:
            return part
    return tilted
