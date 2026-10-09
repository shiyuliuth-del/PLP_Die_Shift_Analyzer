"""Source-material motion on a constrained metal mesh, including holes/branches.

No displaced pad is unioned with stationary copper. Every output contour point
is the image of a source point through one continuous displacement field.
"""
import math
import numpy as np
import shapely
from shapely.geometry import Polygon,Point
from shapely.strtree import STRtree
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra
from .preview import rigid_transform
from .deformation import smooth_weight


class MetalMesh:
    def __init__(self,source,step):
        self.source=shapely.segmentize(source,step)
        if hasattr(shapely,'constrained_delaunay_triangles'):
            triangles=list(shapely.get_parts(shapely.constrained_delaunay_triangles(self.source)))
        else:
            # Shapely 2.0 offline installations: only use an unconstrained
            # tessellation if it covers the source exactly, holes included.
            from shapely.ops import triangulate
            triangles=[p for p in triangulate(self.source) if self.source.covers(p)]
            if shapely.union_all(triangles).symmetric_difference(self.source).area>1e-7:
                raise ValueError('当前几何库无法对该源铜生成完整约束网格')
        if not triangles:raise ValueError('源铜约束网格为空')
        raw=np.array([np.asarray(p.exterior.coords)[:3] for p in triangles]);self.xy,ids=np.unique(raw.reshape(-1,2),axis=0,return_inverse=True)
        self.tri=ids.reshape(-1,3);self.polygons=np.array(triangles,object);self.tree=STRtree(triangles)
        v=self.xy[self.tri];self.origin=v[:,0];matrix=np.stack((v[:,1]-v[:,0],v[:,2]-v[:,0]),axis=2)
        self.inverse=np.linalg.inv(matrix);self.points=shapely.points(self.xy)
        edge=np.unique(np.sort(np.vstack((self.tri[:,[0,1]],self.tri[:,[1,2]],self.tri[:,[2,0]])),axis=1),axis=0)
        distance=np.linalg.norm(self.xy[edge[:,0]]-self.xy[edge[:,1]],axis=1)
        self.regions={}
        self.graph=csr_matrix((np.r_[distance,distance],(np.r_[edge[:,0],edge[:,1]],np.r_[edge[:,1],edge[:,0]])),shape=(len(self.xy),len(self.xy)))
    def nodes_near(self,geometries,guard):
        seeds=set()
        for g in geometries:
            distance=shapely.distance(self.points,g);threshold=max(float(distance.min())+1e-7,guard)
            seeds.update(np.flatnonzero(distance<=threshold))
        return np.array(sorted(seeds),int)
    def distance(self,seeds):
        return dijkstra(self.graph,indices=seeds,min_only=True,directed=False)
    def values(self,xy,values):
        xy=np.asarray(xy);q=shapely.points(xy);index=np.full(len(xy),-1,int)
        pairs=self.tree.query(q,predicate='intersects')
        if pairs.size:index[pairs[0]]=pairs[1]
        missing=np.flatnonzero(index<0)
        if len(missing):index[missing]=self.tree.nearest(q[missing])
        uv=np.einsum('nij,nj->ni',self.inverse[index],xy-self.origin[index]);weights=np.column_stack((1-uv.sum(axis=1),uv))
        return np.sum(values[self.tri[index]]*weights,axis=1)
    def gradients(self,values):
        v=values[self.tri];return np.einsum('ni,nij->nj',v[:,1:]-v[:,:1],self.inverse)
    def interval_region(self,values,start,end):
        key=(values.tobytes(),float(start),float(end))
        if key in self.regions:return self.regions[key]
        t=values[self.tri];low=t.min(axis=1);high=t.max(axis=1)
        full=(low>=start)&(high<=end);parts=list(self.polygons[full])
        ids=np.flatnonzero((high>=start)&(low<=end)&~full)
        for ids_ in self.tri[ids]:
            vertices=[(self.xy[i],values[i]) for i in ids_]
            for threshold,lower in ((start,True),(end,False)):
                out=[]
                for a,b in zip(vertices,vertices[1:]+vertices[:1]):
                    ia=a[1]>=threshold if lower else a[1]<=threshold;ib=b[1]>=threshold if lower else b[1]<=threshold
                    if ia:out.append(a)
                    if ia!=ib:
                        u=(threshold-a[1])/(b[1]-a[1]);out.append((a[0]+u*(b[0]-a[0]),threshold))
                vertices=out
                if not vertices:break
            if len(vertices)>=3:
                p=Polygon([v[0] for v in vertices])
                if p.area>1e-9:parts.append(p)
        out=shapely.union_all(parts).intersection(self.source)
        if len(self.regions)>48:self.regions.clear()
        self.regions[key]=out;return out


class MaterialField:
    mode='material_geodesic'
    def __init__(self,mesh,records,anchors,centers,states,settings,distance,start,end,seeds):
        self.mesh=mesh;self.records=records;self.anchors=anchors;self.centers=centers;self.states=states;self.settings=settings
        self.distance=distance;self.start=float(start);self.end=float(end);self.span=max(1.,end-start);self.inner=np.zeros(len(records));self.seeds=seeds
        self._support=None;self._band=None
    @property
    def support(self):
        if self._support is None:self._support=self.mesh.interval_region(self.distance,0,self.end) if np.isfinite(self.end) else self.mesh.source
        return self._support
    @property
    def band(self):
        if self._band is None:self._band=self.mesh.interval_region(self.distance,self.start,self.end) if np.isfinite(self.end) else Polygon()
        return self._band
    def weights(self,xy):
        d=self.mesh.values(xy,self.distance)
        w=smooth_weight((d-self.start)/self.span,self.settings.deformation_continuity) if np.isfinite(self.end) else np.ones(len(xy))
        return np.repeat(w[:,None],len(self.records),axis=1)
    def __call__(self,xy):
        xy=np.asarray(xy);owner=self.records[0]['owner'];weight=self.weights(xy)[:,0]
        from .acceleration import blend_points
        return blend_points(xy,weight,self.centers[owner],self.states[owner],self.settings.deformation_method,self.settings.deformation_arc_angle_deg,self.settings.acceleration)
    def jacobian_bound(self):
        return 0. if not np.isfinite(self.end) else math.inf
    def injectivity_certificate(self,source):
        if not np.isfinite(self.end):return True
        if self.settings.deformation_method!='bezier':return False
        owner=self.records[0]['owner'];state=self.states[owner];co,si=math.cos(state[2]),math.sin(state[2])
        values=self.distance[self.mesh.tri];active=(values.max(axis=1)>self.start)&(values.min(axis=1)<self.end)
        xy=self.mesh.xy[self.mesh.tri[active]];grad=self.mesh.gradients(self.distance)[active]
        delta=rigid_transform(xy.reshape(-1,2),self.centers[owner],state).reshape(xy.shape)-xy
        adj=np.array([[co,si],[-si,co]])
        dot=np.einsum('ntj,nj->nt',delta,grad);rotated=np.einsum('ntj,nj->nt',delta@adj.T,grad)
        rate=(1.5 if self.settings.deformation_continuity=='C1' else 1.875)/self.span
        lower=math.cos(state[2]/2)**2-rate*np.maximum(0,np.maximum(dot,rotated).max(axis=1))
        self.injectivity_min=float(lower.min(initial=1.))
        return self.injectivity_min>.02


def material_candidates(source,records,anchors,centers,states,settings,width,dbu,cache):
    if len({r['owner'] for r in records})!=1:return
    key=('metal_mesh',source.wkb,round(max(width*.5,1.),6))
    if key not in cache:
        try:cache[key]=MetalMesh(source,max(width*.5,1.))
        except (ValueError,shapely.errors.GEOSException,shapely.errors.UnsupportedGEOSVersionError):return
    mesh=cache[key]
    # Whole nominal landing circumference plus a short attached lead is rigid.
    patches=[]
    for r in records:
        center=Point(r['center']);radius=max(source.boundary.distance(center),np.linalg.norm(shapely.get_coordinates(r['poly'])-r['center'],axis=1).max())
        patches.append(center.buffer(radius+width*.5,quad_segs=32).intersection(source))
    if anchors:
        fixed_geometry=shapely.union_all(anchors)
        moving_geometry=shapely.union_all([r['poly'] for r in records])
        near_move=shapely.distance(mesh.points,moving_geometry)
        near_fixed=shapely.distance(mesh.points,fixed_geometry)
        valid=near_move<=near_fixed*.75
    else:valid=np.ones(len(mesh.xy),bool)
    seeds=mesh.nodes_near(patches,max(dbu*2,width*.25));seeds=seeds[valid[seeds]]
    if not len(seeds):return
    distance=mesh.distance(seeds)
    if not np.isfinite(distance).all():return
    if not anchors:
        yield MaterialField(mesh,records,anchors,centers,states,settings,distance,0,math.inf,seeds);return
    fixed=mesh.nodes_near(anchors,dbu*2)
    limit=min(float(distance[fixed].min()),float(mesh.values(shapely.get_coordinates(anchors),distance).min()))-dbu*2
    if limit<=width*.2:return
    # Long transition first. Later candidates move a longer source lead rigidly
    # and put the adaptation band farther along the actual copper route.
    for fraction,position in ((1.,0),(.75,0),(.5,0),(.75,1),(.5,.5),(.5,1),(.25,.5),(.25,1)):
        length=limit*fraction;start=(limit-length)*position
        yield MaterialField(mesh,records,anchors,centers,states,settings,distance,start,start+length,seeds)


def landing_material(source,records,width,annulus,dbu,anchors=()):
    """Observed source copper at each landing and its attached short lead."""
    boundary=shapely.get_coordinates(shapely.segmentize(source.boundary,max(width*.25,dbu*4)))
    out=[]
    for r in records:
        center=np.asarray(r['center']);radius=max(source.boundary.distance(Point(center)),np.linalg.norm(shapely.get_coordinates(r['poly'])-center,axis=1).max()+annulus)
        points=boundary[np.linalg.norm(boundary-center,axis=1)<=radius+width*.5+dbu]
        if anchors and len(points):
            cloud=shapely.points(points)
            # A short shared island has no separable circular pad. Preserve
            # the moving terminal's source arc; the neck toward a fixed end
            # is allowed to bend, rather than imposing incompatible rigidity.
            points=points[shapely.distance(cloud,r['poly'])<=shapely.distance(cloud,shapely.union_all(anchors))*.65]
        if len(points)<2:points=np.vstack((points,center))
        out.append((r,points,radius+width*.5))
    return out


def material_certificate(field,landmarks,centers,states,tolerance):
    samples=[];maximum=0.
    if not callable(field):return None
    for record,xy,_ in landmarks:
        actual=field(xy);expected=rigid_transform(xy,centers[record['owner']],states[record['owner']])
        error=float(np.linalg.norm(actual-expected,axis=1).max(initial=0));maximum=max(maximum,error)
        samples.append(dict(via=(record['key'],record['index']),owner=record['owner'],source=xy,mapped=actual,expected=expected))
    return dict(verified=maximum<=tolerance,maximum_error_um=maximum,tolerance_um=tolerance,samples=samples)
