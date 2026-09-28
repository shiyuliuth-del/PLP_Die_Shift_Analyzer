"""Connected three-part fallback: a full-width straight cut on the actual net.

Candidate cuts are derived from opposing source edges. Removing the cut must
separate the moving terminals from every fixed terminal. This is a geometric
trace-back through the copper, including bends and branches, never nearest-DIE
assignment or a new unrelated wire. Ranking favours long, wide, central cuts.
"""
import math
from functools import lru_cache
import numpy as np
import shapely
from shapely.geometry import Polygon,LineString,Point
from shapely.geometry.polygon import orient
from .deformation import Field,smooth_weight

@lru_cache(maxsize=192)
def boundary_tolerant(wkb):
    g=shapely.from_wkb(wkb).buffer(1e-7,quad_segs=1);shapely.prepare(g);return g


class RouteField(Field):
    def __init__(self,records,centers,states,settings,moving,band,start,direction,length,width):
        super().__init__(records,centers,states,np.full(len(records),width),length,settings)
        self.moving=moving;self.band=band;self.start=np.asarray(start);self.direction=np.asarray(direction)
        self.length=length;self.width=width;self.mode='three_part'
        self.support=moving.union(band);self.moving_test=boundary_tolerant(moving.wkb);self.band_test=boundary_tolerant(band.wkb)
    def weights(self,xy):
        t=(xy-self.start)@self.direction/self.length
        inside=shapely.intersects_xy(self.band_test,xy[:,0],xy[:,1])
        weight=np.where(inside,smooth_weight(t,self.settings.deformation_continuity),shapely.intersects_xy(self.moving_test,xy[:,0],xy[:,1]).astype(float))
        return np.repeat(weight[:,None],len(self.records),axis=1)
    def __call__(self,xy):
        # All records in this topological moving segment share one DIE. Do not
        # combine repeated weights as independent fields (which changes C1/C2).
        xy=np.asarray(xy);w=self.weights(xy)[:,0];owner=self.records[0]['owner']
        from .preview import rigid_transform
        delta=rigid_transform(xy,self.centers[owner],self.states[owner])-xy
        if self.settings.deformation_method=='bezier':return xy+w[:,None]*delta
        gamma=math.radians(self.settings.deformation_arc_angle_deg)*(1 if self.states[owner,2]>=0 else -1)
        phase=gamma*w;cot=1/math.tan(gamma/2)
        a=.5*(1-np.cos(phase)+cot*np.sin(phase));b=.5*(cot*(1-np.cos(phase))-np.sin(phase))
        return xy+a[:,None]*delta+b[:,None]*np.column_stack((-delta[:,1],delta[:,0]))
    def jacobian_bound(self):
        owner=self.records[0]['owner'];z=self.states[owner];coords=shapely.get_coordinates(self.band)
        radius=np.linalg.norm(coords-self.centers[owner],axis=1).max()
        rotation=2*abs(math.sin(z[2]/2));gradient=1.5 if self.settings.deformation_continuity=='C1' else 1.875
        gamma=math.radians(self.settings.deformation_arc_angle_deg)
        factor=1 if self.settings.deformation_method=='bezier' else gamma/(2*math.sin(gamma/2))
        return rotation+gradient*factor*(np.linalg.norm(z[:2])+rotation*radius)/self.length


class CombinedRouteField:
    """Independent DIE branches; overlapping motion supports are rejected."""
    mode='three_part'
    def __init__(self,fields):
        self.fields=fields;self.records=[r for f in fields for r in f.records]
        self.inner=np.concatenate([f.inner for f in fields]);self.support=shapely.union_all([f.support for f in fields])
        self.band=shapely.union_all([f.band for f in fields]);self.span=max(f.span for f in fields)
    def jacobian_bound(self):return max(f.jacobian_bound() for f in self.fields)
    def __call__(self,xy):
        xy=np.asarray(xy);out=xy.copy()
        for f in self.fields:out+=f(xy)-xy
        return out


def routed_fields(source,records,anchors,centers,states,settings,width,dbu,foreign,templates=None):
    """Bounded combinations of nonoverlapping cuts for shared multi-DIE nets."""
    from itertools import islice
    owners=sorted({r['owner'] for r in records});beams=[[]]
    for owner in owners:
        mine=[r for r in records if r['owner']==owner]
        stops=anchors+[r['poly'] for r in records if r['owner']!=owner]
        key=(source.wkb,owner,tuple(g.wkb for g in stops))
        raw=templates.get(key) if templates is not None else None
        if raw is None:
            raw=list(islice(straight_candidates(source,mine,stops,width,dbu,foreign),48))
            if templates is not None:
                if len(templates)>=64:templates.pop(next(iter(templates)),None)
                templates[key]=raw
        candidates=[RouteField(mine,centers,states,settings,*q) for q in raw]
        if not candidates:return
        next_beams=[]
        for old in beams:
            for candidate in candidates:
                if any(candidate.support.intersection(f.support).area>dbu*dbu for f in old):continue
                next_beams.append(old+[candidate])
        beams=next_beams[:48]
        if not beams:return
    for beam in beams:
        f=beam[0] if len(beam)==1 else CombinedRouteField(beam)
        yield f,f.span


def straight_candidates(source,records,anchors,minimum_width,dbu,foreign=()):
    """Yield (moving copper, adaptation copper, start, axis, length, width).

    Separating the source graph is stronger than matching a centreline near a
    terminal: all moving pads must be in one side, every fixed pad in the other.
    Full-width rectangle cuts leave C1/C2 joins across the entire conductor.
    """
    if source.geom_type!='Polygon' or not anchors or len({r['owner'] for r in records})!=1:return
    poly=orient(source,sign=1.);edges=[]
    for ring in [poly.exterior,*poly.interiors]:
        xy=np.asarray(ring.coords);a=xy[:-1];delta=np.diff(xy,axis=0);length=np.linalg.norm(delta,axis=1)
        for i in np.flatnonzero(length>=minimum_width*3):edges.append((a[i],delta[i]/length[i],float(length[i])))
    if not edges:return
    # Long contour edges are few even in large routed polygons. Parallel angle
    # and opposite direction filters reject most pairs before geometry work.
    ranked=[]
    for i,(a,u,la) in enumerate(edges):
        for b,v,lb in edges[:i]:
            if u@v>-math.cos(math.radians(2)):continue
            n=np.array([-u[1],u[0]]);width=float((b-a)@n)
            if width<minimum_width*.5:continue
            t0,t1=sorted([float((b-a)@u),float((b+v*lb-a)@u)])
            lo=max(0.,t0);hi=min(la,t1);length=hi-lo
            if length<max(minimum_width*4,width*2):continue
            start=a+u*lo+n*width*.5;end=start+u*length
            if not source.covers(LineString([start,end])):continue
            clearance=min((LineString([start,end]).distance(g)-width/2 for g in foreign),default=math.inf)
            # More space is preferred within equivalent length/width choices.
            ranked.append((length*width**.25,min(clearance,length),start,u,length,width))
    ranked.sort(key=lambda q:(q[0],q[1]),reverse=True)
    tested=set()
    for _,_,base,u,full,width in ranked:
        # Longest centred interval first, with guard length at both corners.
        guard=max(width,minimum_width);available=full-2*guard
        if available<minimum_width*2:continue
        for fraction in (1.,.75,.5):
            length=available*fraction
            for center_fraction in ((.5,) if fraction==1 else (.5,.35,.65)):
                offset=max(guard,min(full-guard-length,full*center_fraction-length/2));start=base+u*offset;end=start+u*length
                identity=tuple(np.round(np.r_[start,end]/dbu).astype(int))
                if identity in tested:continue
                tested.add(identity);normal=np.array([-u[1],u[0]])*(width*.6+dbu*4)
                rectangle=Polygon([start-normal,end-normal,end+normal,start+normal])
                band=source.intersection(rectangle)
                if band.geom_type!='Polygon' or band.is_empty:continue
                rest=list(shapely.get_parts(source.difference(rectangle)))
                moving=[g for g in rest if all(g.covers(r['poly']) for r in records)]
                if len(moving)!=1:continue
                moving=moving[0]
                if any(moving.intersects(g) or band.intersects(g) for g in anchors):continue
                fixed=shapely.union_all([g for g in rest if g is not moving])
                if not all(fixed.covers(g) for g in anchors):continue
                # The moving piece must meet just one end face. No side seam is
                # allowed: otherwise the scalar field would have a discontinuity.
                seam=moving.boundary.intersection(band.boundary)
                if seam.is_empty:continue
                projections=(shapely.get_coordinates(seam)-start)@u
                if np.max(abs(projections),initial=0)<=dbu*5:pass
                elif np.max(abs(projections-length),initial=0)<=dbu*5:start=end;direction=-u
                else:continue
                if np.max(abs(projections),initial=0)<=dbu*5:direction=u
                other=fixed.boundary.intersection(band.boundary)
                if other.is_empty or np.max(abs((shapely.get_coordinates(other)-start)@direction-length),initial=0)>dbu*5:continue
                yield moving,band,start,direction,length,width
