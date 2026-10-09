"""Whole cross-section motion with any number of fixed/moving plateaus.

Unlike radial distance blending, opposite edges at the same station have the
same motion weight. A moving terminal may lie in the middle of a conductor,
between fixed terminals, or on one of several arms. The mapping is accepted
only with a global injectivity certificate and the normal final copper DRC.
"""
import math
import numpy as np
import shapely
from shapely.geometry import Polygon
from .deformation import Field, smooth_weight
from .preview import rigid_transform


class AxialField(Field):
    mode='axial'
    def __init__(self,records,centers,states,settings,origin,direction,intervals,source):
        self.origin=np.asarray(origin);self.direction=np.asarray(direction)
        self.intervals=tuple(intervals)
        self.transitions=tuple((a[1],b[0],a[2],b[2]) for a,b in zip(intervals,intervals[1:]) if a[2]!=b[2])
        span=min(b-a for a,b,_,_ in self.transitions)
        super().__init__(records,centers,states,np.zeros(len(records)),span,settings)
        xy=shapely.get_coordinates(source);n=np.array([-self.direction[1],self.direction[0]])
        s=(xy-self.origin)@self.direction;t=(xy-self.origin)@n
        self.normal_range=(float(t.min()),float(t.max()))
        def rect(a,b):
            lo,hi=self.normal_range
            return Polygon([self.origin+self.direction*x+n*y for x,y in ((a,lo-1),(b,lo-1),(b,hi+1),(a,hi+1))])
        bands=[rect(a,b) for a,b,_,_ in self.transitions]
        self.band=source.intersection(shapely.union_all(bands))
        bounds=[float(s.min()-1),*[x for interval in intervals for x in interval[:2]],float(s.max()+1)]
        pieces=[]
        for a,b in zip(sorted(set(bounds)),sorted(set(bounds))[1:]):
            if self.scalar_weights(np.array([(a+b)/2]))[0]>0:pieces.append(rect(a,b))
        self.support=source.intersection(shapely.union_all([*pieces,*bands]))
    def scalar_weights(self,s):
        result=np.full(len(s),float(self.intervals[0][2]))
        for a,b,wa,wb in self.transitions:
            w=smooth_weight((s-a)/(b-a),self.settings.deformation_continuity)
            result+=np.where(s>=a,(wb-wa)*(1-w),0)
        return np.clip(result,0,1)
    def weights(self,xy):
        w=self.scalar_weights((np.asarray(xy)-self.origin)@self.direction)
        return np.repeat(w[:,None],len(self.records),axis=1)
    def active_edges(self,a,b):
        lo=(a-self.origin)@self.direction;hi=(b-self.origin)@self.direction
        return np.logical_or.reduce([(np.minimum(lo,hi)<=right)&(np.maximum(lo,hi)>=left) for left,right,_,_ in self.transitions])
    def __call__(self,xy):
        xy=np.asarray(xy);w=self.scalar_weights((xy-self.origin)@self.direction)
        owner=self.records[0]['owner'];state=self.states[owner]
        from .acceleration import blend_points
        return blend_points(xy,w,self.centers[owner],state,self.settings.deformation_method,
                            self.settings.deformation_arc_angle_deg,self.settings.acceleration)
    def jacobian_bound(self):
        owner=self.records[0]['owner'];state=self.states[owner];u=self.direction;n=np.array([-u[1],u[0]])
        gradient=1.5 if self.settings.deformation_continuity=='C1' else 1.875
        gamma=math.radians(self.settings.deformation_arc_angle_deg)
        factor=1 if self.settings.deformation_method=='bezier' else gamma/(2*math.sin(gamma/2))
        worst=0.
        for a,b,_,_ in self.transitions:
            xy=np.array([self.origin+u*s+n*t for s in (a,b) for t in self.normal_range])
            travel=np.linalg.norm(rigid_transform(xy,self.centers[owner],state)-xy,axis=1).max()
            worst=max(worst,gradient*factor*travel/(b-a))
        return worst+2*abs(math.sin(state[2]/2))
    def injectivity_certificate(self,source):
        if self.settings.deformation_method!='bezier':return False
        owner=self.records[0]['owner'];state=self.states[owner]
        co,si=math.cos(state[2]),math.sin(state[2]);u=self.direction;n=np.array([-u[1],u[0]])
        if co<=.02:return False
        bounds=[];gradient=1.5 if self.settings.deformation_continuity=='C1' else 1.875
        for a,b,wa,wb in self.transitions:
            corners=np.array([self.origin+u*s+n*t for s in (a,b) for t in self.normal_range])
            delta=rigid_transform(corners,self.centers[owner],state)-corners
            du=delta@u;dv=delta@n;rate=gradient/(b-a);sign=1 if wb>wa else -1
            j11=co+rate*min(0.,float((sign*du).min()))
            det=math.cos(state[2]/2)**2+rate*min(0.,float(np.r_[sign*du,sign*(co*du+si*dv)].min()))
            bounds.extend((j11,co,det))
        self.injectivity_bounds=tuple(bounds)
        return min(bounds,default=0)>.02


def axial_candidates(source,records,anchors,centers,states,settings,width,dbu,limit=12):
    if not anchors or len({r['owner'] for r in records})!=1:return
    points=shapely.get_coordinates(source);origin=points.mean(axis=0)
    # Principal directions and actual straight edges cover both islands and
    # long traces. Keep real angles; quantization is only used to deduplicate.
    _,_,vh=np.linalg.svd(points-origin,full_matrices=False)
    moving=np.mean([r['center'] for r in records],axis=0)
    vectors=[*vh,*[np.asarray(g.centroid.coords[0])-moving for g in anchors]]
    for p in shapely.get_parts(source):
        for ring in [p.exterior,*p.interiors]:
            edge=np.diff(np.asarray(ring.coords),axis=0)
            vectors.extend(edge[np.linalg.norm(edge,axis=1)>width*2])
    ranked=[];seen=set()
    for v in vectors:
        length=np.linalg.norm(v)
        if length<dbu:continue
        u=v/length
        if u[0]<0 or (abs(u[0])<1e-10 and u[1]<0):u=-u
        angle=round(math.atan2(u[1],u[0]),3)
        if angle in seen:continue
        seen.add(angle);raw=[]
        for geometry,value in [*[(r['poly'],1) for r in records],*[(g,0) for g in anchors]]:
            s=(shapely.get_coordinates(geometry)-origin)@u
            raw.append((float(s.min()-2*dbu),float(s.max()+2*dbu),value))
        raw.sort();merged=[];valid=True
        for a,b,value in raw:
            if merged and a<=merged[-1][1]:
                if value!=merged[-1][2]:valid=False;break
                merged[-1]=(merged[-1][0],max(b,merged[-1][1]),value)
            elif merged and value==merged[-1][2]:merged[-1]=(merged[-1][0],b,value)
            else:merged.append((a,b,value))
        if not valid or len(merged)<2:continue
        gaps=[b[0]-a[1] for a,b in zip(merged,merged[1:])]
        if min(gaps)<max(width*.2,dbu*10):continue
        ranked.append((min(gaps),sum(gaps),u,merged))
    ranked.sort(key=lambda q:(q[0],q[1]),reverse=True)
    # Try spatially local transitions too: a long net may be blocked far from
    # the via although its immediate corridor is free. Extending only fixed
    # plateaus never consumes the moving terminal or crosses an anchor.
    for fraction in (1.,.65,.4):
        for _,_,u,intervals in ranked[:max(1,limit//3)]:
            local=[list(v) for v in intervals]
            for i,(a,b) in enumerate(zip(intervals,intervals[1:])):
                gap=b[0]-a[1]
                if a[2]==0:local[i][1]=b[0]-gap*fraction
                else:local[i+1][0]=a[1]+gap*fraction
            yield AxialField(records,centers,states,settings,origin,u,local,source)
