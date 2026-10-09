"""Local landing reconstruction retaining every incoming copper branch.

Trim only the obsolete landing neighbourhood, retain fixed terminal copper,
then join the remaining components through short constant-width connectors.
These are candidates: the normal exact enclosure, topology and DRC gates apply.
"""
import math
import numpy as np
import shapely
from shapely.geometry import Polygon,Point,LineString
from shapely.geometry.polygon import orient
from shapely.ops import nearest_points
from .preview import rigid_transform


def detour(a,b,source,support,forbidden,radius,tolerance):
    """Visibility routing of connector centres; final copper DRC is separate."""
    margin=max(.1,tolerance*2)
    free=support.buffer(-radius-margin).difference(forbidden.buffer(radius+margin,quad_segs=16))
    free=free.union(source.buffer(-radius-margin)).intersection(support.buffer(radius))
    domain=free.buffer(1e-8);shapely.prepare(domain)
    points=np.asarray([a.coords[0],b.coords[0]])
    if not np.all(shapely.intersects_xy(domain,points[:,0],points[:,1])):return None
    vertices=shapely.get_coordinates(free.simplify(margin*.25,preserve_topology=True))
    if not len(vertices):return None
    score=np.linalg.norm(vertices-points[0],axis=1)+np.linalg.norm(vertices-points[1],axis=1)
    vertices=vertices[np.argsort(score)[:180]]
    nodes=np.vstack((points,np.unique(vertices,axis=0)));i,j=np.triu_indices(len(nodes),1)
    segments=shapely.linestrings(np.stack((nodes[i],nodes[j]),axis=1));visible=shapely.covers(domain,segments)
    distances=np.linalg.norm(nodes[i]-nodes[j],axis=1);visible&=distances>1e-9
    from scipy.sparse import csr_matrix
    from scipy.sparse.csgraph import dijkstra
    row=np.r_[i[visible],j[visible]];col=np.r_[j[visible],i[visible]];weight=np.r_[distances[visible],distances[visible]]
    graph=csr_matrix((weight,(row,col)),shape=(len(nodes),len(nodes)))
    cost,previous=dijkstra(graph,indices=0,return_predecessors=True)
    if not np.isfinite(cost[1]):return None
    ids=[1]
    while ids[-1]!=0:
        ids.append(int(previous[ids[-1]]))
        if ids[-1]<0:return None
    path=nodes[ids[::-1]];rounded=[path[0]]
    for k in range(1,len(path)-1):
        u=path[k]-path[k-1];v=path[k+1]-path[k];lu=np.linalg.norm(u);lv=np.linalg.norm(v);u/=lu;v/=lv
        cut=min(lu*.4,lv*.4,margin*.4);p0=path[k]-cut*u;p1=path[k]+cut*v;h=cut*.35
        controls=np.array([p0,p0+h*u,p0+2*h*u,p1-2*h*v,p1-h*v,p1])
        count=max(8,min(128,math.ceil(math.sqrt(max(cut,tolerance)/max(tolerance,1e-6))*4)))
        t=np.linspace(0,1,count);basis=np.array([math.comb(5,n)*(1-t)**(5-n)*t**n for n in range(6)]).T
        rounded.extend(basis@controls)
    rounded.append(path[-1]);return LineString(rounded)


def fair_concave_joins(geometry,support,tolerance,width,minimum=None):
    """Quintic fillets have zero endpoint curvature against adjacent segments."""
    def ring(coords):
        xy=np.asarray(coords)[:-1];out=[]
        for i,p in enumerate(xy):
            a=p-xy[i-1];b=xy[(i+1)%len(xy)]-p;la=np.linalg.norm(a);lb=np.linalg.norm(b)
            if min(la,lb)<1e-12:continue
            u=a/la;v=b/lb;turn=math.atan2(u[0]*v[1]-u[1]*v[0],float(u@v))
            eligible=turn<-.07 if minimum is None else turn>math.radians(180-minimum+.2)
            if eligible and support.covers(Point(p)):
                cut=min(width*.5,.42*la,.42*lb);p0=p-cut*u;p1=p+cut*v;h=cut*.35
                controls=np.array([p0,p0+h*u,p0+2*h*u,p1-2*h*v,p1-h*v,p1])
                count=max(5,min(128,math.ceil(math.sqrt(max(cut,tolerance)/max(tolerance,1e-6))*4)))
                t=np.linspace(0,1,count)
                basis=np.array([math.comb(5,j)*(1-t)**(5-j)*t**j for j in range(6)]).T
                out.extend(basis@controls)
            else:out.append(p)
        return out
    parts=[]
    for raw in shapely.get_parts(geometry):
        if raw.geom_type!='Polygon':continue
        g=orient(raw,sign=1.)
        parts.append(Polygon(ring(g.exterior.coords),[ring(r.coords) for r in g.interiors]))
    result=shapely.union_all(parts)
    return result if result.is_valid else geometry


class LocalRebuild:
    mode='local_rebuild'
    def __init__(self,source,records,anchors,centers,states,settings,annulus,width,dbu,trim_scale):
        self.source=source;self.records=records;self.anchors=anchors;self.centers=centers;self.states=states;self.settings=settings
        self.annulus=annulus;self.width=width;self.dbu=dbu;self.trim_scale=trim_scale
        self.inner=np.zeros(len(records));self.span=max(width*4,1.);self.support=source
        self.obstacles=Polygon();self.clip_clearance=False
    def refresh_support(self):
        supports=[]
        for r in self.records:
            old=np.asarray(r['center']);moved=rigid_transform(old[None],self.centers[r['owner']],self.states[r['owner']])[0]
            radius=max(np.linalg.norm(shapely.get_coordinates(r['poly'])-old,axis=1).max()+self.annulus,self.source.boundary.distance(Point(old)))
            supports.append(LineString([old,moved]).buffer(radius+self.width*(max(0,self.trim_scale)+2),quad_segs=32))
        self.support=shapely.union_all(supports)
    def candidate_geometry(self,tolerance,dbu):
        lands=[];cuts=[];supports=[]
        for r in self.records:
            old=np.asarray(r['center']);z=self.states[r['owner']]
            moved=shapely.transform(r['poly'],lambda xy:rigid_transform(xy,self.centers[r['owner']],z))
            required=moved.buffer(self.annulus/math.cos(math.pi/128)+2*dbu,quad_segs=32)
            lands.append(required)
            radius=max(np.linalg.norm(shapely.get_coordinates(r['poly'])-old,axis=1).max()+self.annulus,self.source.boundary.distance(Point(old)))
            disk=Point(old).buffer(radius+self.width*self.trim_scale,quad_segs=32)
            cuts.append(disk);supports.append(shapely.union_all([disk,required]).convex_hull.buffer(self.width*2))
        self.support=shapely.union_all(supports)
        protected=[]
        for anchor in self.anchors:
            center=anchor.centroid;radius=max(self.source.boundary.distance(center),self.width)
            protected.append(center.buffer(radius,quad_segs=32).union(anchor))
        cut=shapely.union_all(cuts).difference(shapely.union_all(protected))
        remainder=self.source if self.trim_scale<0 else self.source.difference(cut)
        if self.trim_scale>=0:
            # Circular pad removal may leave a tiny cap at the old square
            # shoulder. It has no terminal and no continuation outside the
            # edited neighbourhood; connecting it back creates a dead spur.
            anchor_region=shapely.union_all(protected)
            remainder=shapely.union_all([g for g in shapely.get_parts(remainder)
                if g.area>self.width**2 or not self.support.covers(g) or g.intersects(anchor_region)])
        combined=shapely.union_all([remainder,*lands])
        radius=self.width/2+2*dbu
        for _ in range(2*len(self.records)+16):
            parts=[p for p in shapely.get_parts(combined) if p.geom_type=='Polygon']
            if len(parts)==1:break
            cores=[p.buffer(-radius,join_style=2) for p in parts]
            cores=[g if not g.is_empty else p.representative_point() for g,p in zip(cores,parts)]
            pairs=sorted((cores[i].distance(cores[j]),i,j) for i in range(len(parts)) for j in range(i))
            joined=False
            for _,i,j in pairs:
                a,b=nearest_points(cores[i],cores[j]);connector=LineString([a,b]).buffer(radius,quad_segs=32)
                if not self.support.buffer(dbu).covers(connector.difference(self.source)):continue
                if connector.difference(self.source).intersects(self.obstacles):
                    routed=detour(a,b,self.source,self.support,self.obstacles,radius,tolerance)
                    if routed is None:continue
                    connector=routed.buffer(radius,quad_segs=32)
                candidate=shapely.union_all([combined,connector])
                if len(shapely.get_parts(candidate))<len(parts):combined=candidate;joined=True;break
            if not joined:raise ValueError('局部连接走廊未找到')
        if combined.geom_type!='Polygon':raise ValueError('局部重建仍有断开铜区')
        # Only holes introduced by removing an obsolete landing may be filled.
        # Original manufacturing-scale holes remain independent constraints.
        old_holes=[Polygon(r) for r in self.source.interiors]
        retained=[r for r in combined.interiors if any(Polygon(r).intersects(g) for g in old_holes)]
        combined=Polygon(combined.exterior,retained)
        result=fair_concave_joins(combined,self.support,tolerance,self.width)
        # A local fillet can bulge into an already constrained neighbourhood.
        # Preserve existing copper, but trim newly added copper to the same
        # forbidden domain used by the router. Exact enclosure/width/spacing
        # and angle gates still decide whether this contour can be accepted.
        if self.clip_clearance and not self.obstacles.is_empty:
            from .deformation import snapped
            reference=snapped(self.source,dbu)[0]
            result=result.difference(self.obstacles.buffer(2*dbu).difference(reference))
        if result.geom_type!='Polygon' or not result.is_valid:raise ValueError('局部重建产生无效轮廓')
        return result
