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
        self.support=moving.union(band);shapely.prepare(self.support);self.moving_test=boundary_tolerant(moving.wkb);self.band_test=boundary_tolerant(band.wkb)
    def active_edges(self,a,b):
        x0,y0,x1,y1=self.band.bounds;lo=np.minimum(a,b);hi=np.maximum(a,b)
        return (lo[:,0]<=x1)&(hi[:,0]>=x0)&(lo[:,1]<=y1)&(hi[:,1]>=y0)
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
        from .preview import rigid_transform
        travel=np.linalg.norm(rigid_transform(coords,self.centers[owner],z)-coords,axis=1).max()
        rotation=2*abs(math.sin(z[2]/2));gradient=1.5 if self.settings.deformation_continuity=='C1' else 1.875
        gamma=math.radians(self.settings.deformation_arc_angle_deg)
        factor=1 if self.settings.deformation_method=='bezier' else gamma/(2*math.sin(gamma/2))
        return rotation+gradient*factor*travel/self.length


class CombinedRouteField:
    """Independent DIE branches; overlapping motion supports are rejected."""
    mode='three_part'
    def __init__(self,fields):
        self.fields=fields;self.records=[r for f in fields for r in f.records]
        self.inner=np.concatenate([f.inner for f in fields]);self.support=shapely.union_all([f.support for f in fields])
        self.band=shapely.union_all([f.band for f in fields]);self.span=max(f.span for f in fields)
    def jacobian_bound(self):return max(f.jacobian_bound() for f in self.fields)
    def active_edges(self,a,b):return np.logical_or.reduce([f.active_edges(a,b) for f in self.fields])
    def __call__(self,xy):
        xy=np.asarray(xy);out=xy.copy()
        for f in self.fields:out+=f(xy)-xy
        return out


class SlabField(RouteField):
    """Smooth whole-section transition, including bent/branched conductors.

    Every point before/after the two cutting planes is rigid/fixed. Thus all
    seams, including multiple arms crossing a section, share endpoint jets.
    """
    def weights(self,xy):
        t=(xy-self.start)@self.direction/self.length
        if getattr(self,'gentle_profile',False):
            # C2 integral of a smoothstep-ramped derivative. A long linear
            # middle limits peak slope to 1.25 instead of quintic's 1.875.
            t=np.clip(t,0,1);r=.2;u=np.minimum(t,1-t)/r
            edge=r/(1-r)*(u**3-.5*u**4)
            integral=np.where(t<r,edge,np.where(t>1-r,1-edge,(t-r/2)/(1-r)))
            w=1-integral
        else:w=smooth_weight(t,self.settings.deformation_continuity)
        return np.repeat(w[:,None],len(self.records),axis=1)
    def active_edges(self,a,b):
        left=(a-self.start)@self.direction;right=(b-self.start)@self.direction
        return (np.minimum(left,right)<=self.length)&(np.maximum(left,right)>=0)

    def injectivity_certificate(self,source):
        """P-matrix certificate on a rectangle enclosing the WHOLE source.

        A transverse shear can be injective even when ||DF-I|| > 1. In the
        route basis, bound J11, J22 and det(J) over the entire transition slab.
        Outside it the field is rigid or identity. Strictly positive principal
        minors certify global injectivity on this rectangle (Gale-Nikaido).
        Only the Bezier-weight affine blend has this analytic certificate.
        """
        if self.settings.deformation_method!='bezier':return False
        owner=self.records[0]['owner'];state=self.states[owner];co=math.cos(state[2]);si=math.sin(state[2])
        if co<=.02:return False
        u=self.direction;n=np.array([-u[1],u[0]]);xy=shapely.get_coordinates(source)
        v=(xy-self.start)@n;lo,hi=float(v.min()),float(v.max())
        corners=np.array([self.start+u*s+n*t for s in (0,self.length) for t in (lo,hi)])
        from .preview import rigid_transform
        delta=rigid_transform(corners,self.centers[owner],state)-corners;du=delta@u;dv=delta@n
        rate=(1.25 if getattr(self,'gentle_profile',False) else 1.5 if self.settings.deformation_continuity=='C1' else 1.875)/self.length
        # w in [0,1], w' in [-rate,0]. Delta is affine in the rectangle.
        determinant=math.cos(state[2]/2)**2-rate*max(0.,float(np.max(np.r_[du,co*du+si*dv])))
        diagonal=co-rate*max(0.,float(du.max()))
        self.injectivity_bounds=(diagonal,co,determinant)
        return min(self.injectivity_bounds)>.02


def section_candidates(source,records,anchors,minimum_width,dbu,settings):
    """Cut whole sections rather than requiring one pair of long straight edges.

    This family permits curved edges, broad landing islands, holes and branches;
    terminal coverage and later full DRC remain mandatory. It does not invent a
    connection across an empty source region.
    """
    if not anchors or len({r['owner'] for r in records})!=1:return
    moving_points=shapely.get_coordinates([r['poly'] for r in records])
    fixed_points=shapely.get_coordinates(anchors)
    all_points=shapely.get_coordinates(source);origin=moving_points.mean(axis=0)
    directions=[fixed_points.mean(axis=0)-origin]
    for anchor in anchors:directions.append(np.asarray(anchor.centroid.coords[0])-origin)
    for poly in shapely.get_parts(source):
        for ring in [poly.exterior,*poly.interiors]:
            edges=np.diff(np.asarray(ring.coords),axis=0)
            directions.extend(edges[np.linalg.norm(edges,axis=1)>=minimum_width])
    seen=set();ranked=[]
    for v in directions:
        norm=np.linalg.norm(v)
        if norm<dbu:continue
        u=v/norm
        if u@(fixed_points.mean(axis=0)-origin)<0:u=-u
        # Keep observed orientations (not a rounded replacement direction).
        key=round(math.atan2(u[1],u[0])*180/math.pi,1)
        if key in seen:continue
        seen.add(key)
        low=float(((moving_points-origin)@u).max());high=float(((fixed_points-origin)@u).min())
        guard=settings.deformation_corner_guard*minimum_width
        lo=low+guard;hi=high-guard;length=hi-lo
        if length<settings.deformation_min_length*minimum_width:continue
        n=np.array([-u[1],u[0]]);side=(all_points-origin)@n
        n0,n1=float(side.min()-minimum_width),float(side.max()+minimum_width)
        extent=max(np.linalg.norm(all_points-origin,axis=1).max(),minimum_width)*3
        def rectangle(a,b):
            return Polygon([origin+u*a+n*n0,origin+u*b+n*n0,origin+u*b+n*n1,origin+u*a+n*n1])
        for fraction,position in [(1.,.5),(.75,.5),(.5,.5),(.5,.25),(.5,.75)]:
            size=length*fraction;start=lo+(length-size)*position;end=start+size
            moving=source.intersection(rectangle(-extent,start));band=source.intersection(rectangle(start,end))
            fixed=source.intersection(rectangle(end,extent))
            if band.is_empty or moving.is_empty or fixed.is_empty:continue
            if not all(moving.covers(r['poly']) for r in records) or not all(fixed.covers(g) for g in anchors):continue
            if any(moving.intersects(g) or band.intersects(g) for g in anchors):continue
            # Reject unattached bands or missing geometric necks. Source topology
            # is then independently checked after warp + DBU conversion.
            if not moving.intersects(band) or not fixed.intersects(band):continue
            width=band.area/size
            ranked.append((size, width, (moving,band,origin+u*start,u,size,width)))
    ranked.sort(key=lambda q:(q[0],q[1]),reverse=True)
    for _,_,candidate in ranked:yield candidate


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
            count=settings.deformation_route_candidates
            # Reserve capacity for general sections; straight-only enumeration
            # must not exhaust the budget before curved/branched shapes are tried.
            raw=[('straight',q) for q in islice(straight_candidates(source,mine,stops,width,dbu,foreign,settings),max(1,count//2) if settings.deformation_general_bands else count)]
            if settings.deformation_general_bands:
                raw.extend(('section',q) for q in islice(section_candidates(source,mine,stops,width,dbu,settings),count-len(raw)))
            if templates is not None:
                templates[key]=raw
        candidates=[(SlabField if kind=='section' else RouteField)(mine,centers,states,settings,*q) for kind,q in raw]
        if not candidates:return
        next_beams=[]
        for old in beams:
            for candidate in candidates:
                def overlaps(other):
                    k=('support_overlap',candidate.support,other.support)
                    if templates is not None and k in templates:return templates[k]
                    value=candidate.support.intersects(other.support) and candidate.support.intersection(other.support).area>dbu*dbu
                    if templates is not None:templates[k]=value
                    return value
                if any(overlaps(f) for f in old):continue
                next_beams.append(old+[candidate])
        beams=next_beams[:settings.deformation_route_candidates]
        if not beams:return
    for beam in beams:
        f=beam[0] if len(beam)==1 else CombinedRouteField(beam)
        yield f,f.span


def straight_candidates(source,records,anchors,minimum_width,dbu,foreign=(),settings=None):
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
        guard=max(width,minimum_width)*(settings.deformation_corner_guard if settings else 1.);available=full-2*guard
        if available<minimum_width*(settings.deformation_min_length if settings else 2):continue
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
