"""Nominal, topology-constrained routes shared by every preview pose.

The medial graph is a navigation aid only. Every cut is measured against the
original polygon, and must separate its moving terminals from fixed terminals.
No nearest-chip assignment is performed here. Units are micrometres.
"""
from dataclasses import dataclass,field
from functools import lru_cache
import hashlib
from collections import OrderedDict
import heapq
import math
import numpy as np
import shapely
from shapely.ops import split
from scipy.spatial import Voronoi,cKDTree,QhullError

from .resources import cancel_check

EMPTY=shapely.Polygon()


def part_key(part):
    return (part['a'],part['b'],*part.get('angles',(0.,0.)))


def parts(g):
    if g is None or g.is_empty:return []
    if g.geom_type=='Polygon':return [g]
    return [p for child in getattr(g,'geoms',()) for p in parts(child)]


@lru_cache(maxsize=192)
def medial_graph(wkb,width):
    poly=shapely.from_wkb(wkb)
    step=max(float(width)/3,poly.length/3500,1e-4)
    boundary=shapely.segmentize(poly.boundary,step)
    xy=np.unique(shapely.get_coordinates(boundary),axis=0)
    try:vor=Voronoi(xy)
    except QhullError:return np.empty((0,2)),()
    nodes=vor.vertices
    inside=shapely.contains_xy(poly,nodes[:,0],nodes[:,1])
    pairs=np.array([r for r in vor.ridge_vertices if len(r)==2 and min(r)>=0],dtype=int).reshape(-1,2)
    pairs=pairs[inside[pairs].all(axis=1)]
    if not len(pairs):return np.empty((0,2)),()
    segments=shapely.linestrings(nodes[pairs]);pairs=pairs[shapely.covers(poly,segments)]
    graph=[[] for _ in nodes]
    for i,j in pairs:
        length=float(np.linalg.norm(nodes[i]-nodes[j]))
        if length>1e-10:graph[i].append((int(j),length));graph[j].append((int(i),length))
    return nodes,tuple(tuple(v) for v in graph)


def trace(poly,start,end,width):
    """Shortest interior medial path, with exact source terminal endpoints."""
    start,end=np.asarray(start,float),np.asarray(end,float)
    if np.linalg.norm(end-start)<1e-8:return None
    nodes,base=medial_graph(poly.wkb,round(float(width),6))
    if not len(nodes):
        direct=shapely.LineString([start,end])
        return direct if poly.covers(direct) else None
    active=np.array([i for i,row in enumerate(base) if row],int)
    if not len(active):return None
    tree=cKDTree(nodes[active]);graph=[list(row) for row in base];graph.extend([[],[]])
    allxy=np.vstack([nodes,start,end]);src,dst=len(nodes),len(nodes)+1
    for pos,point in ((src,start),(dst,end)):
        _,near=tree.query(point,k=min(24,len(active)))
        for i in active[np.atleast_1d(near)]:
            if poly.covers(shapely.LineString([point,nodes[i]])):
                d=float(np.linalg.norm(point-nodes[i]));graph[pos].append((int(i),d));graph[i].append((pos,d))
    queue=[(0.,src)];dist={src:0.};previous={}
    while queue:
        d,i=heapq.heappop(queue)
        if d!=dist.get(i):continue
        if i==dst:break
        for j,w in graph[i]:
            value=d+w
            if value<dist.get(j,math.inf):dist[j]=value;previous[j]=i;heapq.heappush(queue,(value,j))
    if dst not in previous:return None
    order=[dst]
    while order[-1]!=src:order.append(previous[order[-1]])
    line=shapely.LineString(allxy[order[::-1]])
    # Remove numerical teeth of the discretised medial axis, without crossing
    # holes or leaving source copper. A rejected simplification retains the graph.
    simple=line.simplify(max(width*.07,1e-4),preserve_topology=False)
    return simple if poly.covers(simple) else line


def project(route,points):
    return shapely.line_locate_point(route,shapely.points(np.asarray(points,float)))


def branch_sources(source,land,anchors,start,width,tolerance,guard):
    """Separate tree-like fixed branches, sharing one rigid moving trunk.

    A branch cut must isolate exactly its own fixed landing. Loops or inseparable
    terminals keep the existing whole-source warning path instead of inventing
    a nearest-centre partition or cutting through another terminal.
    """
    if not 1<len(anchors)<=8:return None
    distal=[];probes=[];positions=[];limits=[]
    for anchor in anchors:
        route=trace(source,start,anchor.representative_point().coords[0],width)
        if route is None:return None
        others=tuple([g for g in anchors if g is not anchor]+[land])
        probe=RoutePlan('',0,0,(),source,route,anchor,others,tolerance,guard)
        lo=float(np.max(project(route,shapely.get_coordinates(land))))+max(tolerance*16,guard)
        hi=float(np.min(project(route,shapely.get_coordinates(anchor))))-max(tolerance*16,guard)
        if hi<=lo:return None
        lower=lo;found=None
        for mark in np.linspace(lo,hi,32):
            found=probe.prefix(float(mark))
            if found is not None:
                upper=float(mark)
                for _ in range(7):
                    middle=(lower+upper)/2;candidate=probe.prefix(middle)
                    if candidate is None:lower=middle
                    else:upper=middle;found=candidate
                break
            lower=float(mark)
        if found is None:return None
        distal.append(found[0]);probes.append(probe);positions.append(upper);limits.append(hi)
    epsilon=max(tolerance*tolerance,source.area*1e-10)
    for _ in range(16):
        bad=set()
        for i,a in enumerate(distal):
            for j in range(i+1,len(distal)):
                if a.intersection(distal[j]).area>epsilon:bad.update((i,j))
        trunk=source.difference(shapely.union_all(distal))
        if trunk.is_empty or land.difference(trunk).area>epsilon:return None
        result=[(shapely.union_all([trunk,g]),trunk,anchor) for g,anchor in zip(distal,anchors)]
        if trunk.geom_type!='Polygon' or any(g.geom_type!='Polygon' or not g.is_valid for g,_,_ in result):bad.update(range(len(distal)))
        if not bad:
            if shapely.union_all([g for g,_,_ in result]).symmetric_difference(source).area>epsilon:return None
            return result
        for i in sorted(bad):
            mark=min(limits[i],positions[i]+max(width,guard*2))
            if mark<=positions[i]+tolerance:return None
            found=probes[i].prefix(mark)
            if found is None:return None
            distal[i]=found[0];positions[i]=mark
    return None


@dataclass
class RoutePlan:
    layer:str
    index:int
    owner:int
    refs:tuple
    source:object
    route:object
    land:object
    anchors:tuple
    tolerance:float
    guard:float
    nominal_first:float=0.
    nominal_second:float=0.
    warning:str=''
    memo_budget:int=1024*1024
    cache:dict=field(default_factory=dict,repr=False)

    def __post_init__(self):
        self.pose_memo=OrderedDict();self.memo_bytes=0
        self.xy=np.asarray(self.route.coords)
        self.lengths=np.linalg.norm(np.diff(self.xy,axis=0),axis=1)
        self.marks=np.r_[0,np.cumsum(self.lengths)]
        self.length=float(self.route.length)
        self.identity=hashlib.sha256(self.route.wkb).hexdigest()[:20]
        self.land_end=float(np.max(project(self.route,shapely.get_coordinates(self.land))))
        self.fixed_start=min((float(np.min(project(self.route,shapely.get_coordinates(g)))) for g in self.anchors),default=self.length)
        self.fixed_start=max(0.,min(self.fixed_start,self.length))
        self.corners=[]
        for i in range(1,len(self.xy)-1):
            u=(self.xy[i]-self.xy[i-1])/self.lengths[i-1];v=(self.xy[i+1]-self.xy[i])/self.lengths[i]
            if float(u@v)<math.cos(math.radians(8)):
                radius=max(self.guard,float(shapely.Point(self.xy[i]).distance(self.source.boundary))*.65)
                self.corners.append((float(self.marks[i]),radius))

    def __getstate__(self):
        state=dict(self.__dict__);state['cache']={};state['pose_memo']=OrderedDict();state['memo_bytes']=0;return state

    @property
    def key(self):return self.layer,self.index,self.owner,self.refs

    def tangent(self,s):
        i=min(len(self.lengths)-1,max(0,int(np.searchsorted(self.marks,s,side='right')-1)))
        return (self.xy[i+1]-self.xy[i])/max(self.lengths[i],1e-12)

    def snap(self,s,which,other):
        eps=max(self.tolerance*8,1e-4)
        lo=0. if which==0 else max(other+eps,self.land_end+eps)
        hi=min(other-eps,self.fixed_start-eps) if which==0 else self.length
        if hi<lo:return lo
        s=float(np.clip(s,lo,hi))
        if which==0 and s<=self.land_end+eps:return 0.
        if which==1 and s>=self.fixed_start-eps:return self.length
        for corner,radius in self.corners:
            if corner-radius<s<corner+radius:
                options=[x for x in (corner-radius,corner+radius) if lo<=x<=hi]
                if options:s=min(options,key=lambda x:abs(x-s))
                else:s=lo if which==0 else hi
        return s

    def section(self,s,angle=0.):
        point=np.asarray(self.route.interpolate(s).coords[0]);normal=self.tangent(s);u=np.array([-normal[1],normal[0]])
        co,si=np.cos(angle),np.sin(angle);u=np.array([co*u[0]-si*u[1],si*u[0]+co*u[1]])
        size=np.hypot(self.source.bounds[2]-self.source.bounds[0],self.source.bounds[3]-self.source.bounds[1])+1
        chord=shapely.intersection(self.source,shapely.LineString([point-u*size,point+u*size]))
        lines=[q for q in shapely.get_parts(chord) if q.geom_type=='LineString' and q.length>self.tolerance]
        if not lines:return None
        return min(lines,key=lambda g:float(g.distance(shapely.Point(point))))

    def prefix(self,s,angle=0.):
        """Connected source portion on the moving-terminal side of a local cut."""
        key=('prefix',round(float(s),8),round(float(angle),10))
        if key in self.cache:return self.cache[key]
        face=self.section(s,angle)
        if face is None:return None
        xy=np.asarray(face.coords);v=xy[-1]-xy[0];v/=max(np.linalg.norm(v),1e-12)
        blade=shapely.LineString([xy[0]-v*self.tolerance*16,xy[-1]+v*self.tolerance*16])
        try:pieces=parts(split(self.source,blade))
        except (ValueError,shapely.errors.GEOSException):return None
        eps=max(self.tolerance**2,self.land.area*1e-7)
        candidates=[g for g in pieces if g.intersection(self.land).area>=self.land.area-eps]
        candidates=[g for g in candidates if all(g.intersection(a).area<=eps for a in self.anchors)]
        if len(pieces)<2 or len(candidates)!=1:result=None
        else:result=candidates[0],face,shapely.union_all([g for g in pieces if g is not candidates[0]])
        if len(self.cache)>512:self.cache.clear()
        self.cache[key]=result
        return result

    def partition(self,a,b,angles=(0.,0.)):
        """Return disjoint A/B/C and their real shared boundaries, or no partition."""
        if abs(b-self.length)<=self.tolerance*.01:
            moving_plus=self.source;far=shapely.Point(self.route.coords[-1]);b_shape=EMPTY
        else:
            outer=self.prefix(b,angles[1])
            if outer is None:return None
            moving_plus,far,b_shape=outer
        if a==0.:
            c=EMPTY;near=shapely.Point(self.route.coords[0]);a_shape=moving_plus
        else:
            near=self.section(a,angles[0])
            if near is None:return None
            xy=np.asarray(near.coords);v=xy[-1]-xy[0];v/=max(np.linalg.norm(v),1e-12)
            blade=shapely.LineString([xy[0]-v*self.tolerance*16,xy[-1]+v*self.tolerance*16])
            try:pieces=parts(split(moving_plus,blade))
            except (ValueError,shapely.errors.GEOSException):return None
            epsilon=max(self.tolerance**2,self.land.area*1e-7)
            movable=[g for g in pieces if g.intersection(self.land).area>=self.land.area-epsilon]
            if len(movable)!=1 or len(pieces)<2:return None
            c=movable[0];a_shape=shapely.union_all([g for g in pieces if g is not c])
        if a_shape.is_empty:return None
        union=shapely.union_all([a_shape,b_shape,c])
        if union.symmetric_difference(self.source).area>max(self.tolerance**2,self.source.area*1e-8):return None
        return {'A':a_shape,'B':b_shape,'C':c,'near':near,'far':far,'a':a,'b':b,'angles':tuple(map(float,angles))}

    def pad_handle(self,side):
        """UI-only branchward point; electrical endpoint and export stay exact."""
        origin=np.asarray(self.route.coords[0 if side==0 else -1],float)
        position=min(self.length,max(self.land_end+self.guard,self.length*.25)) if side==0 else max(0.,min(self.fixed_start-self.guard,self.length*.75))
        toward=np.asarray(self.route.interpolate(position).coords[0])-origin
        pad=self.land if side==0 else min(self.anchors,key=lambda g:g.distance(shapely.Point(origin)),default=self.source)
        radius=float(shapely.Point(origin).distance(pad.boundary))
        length=np.linalg.norm(toward)
        if length<self.tolerance:return shapely.Point(origin)
        return shapely.Point(origin+toward/length*min(radius*.45,length*.2,20.))

    def choose(self,max_travel):
        """Longest straight candidate; motion envelope determines its margin.

        This is a bounded engineering search, not a global routing optimum.
        The same full-input displacement bound is used in every pose.
        """
        eps=max(self.tolerance*16,.01)
        breaks=[(self.land_end+eps,0.),*self.corners,(self.fixed_start-eps,0.)]
        candidates=[]
        for (left,g0),(right,g1) in zip(breaks,breaks[1:]):
            lo=max(self.land_end+eps,left+g0);hi=min(self.fixed_start-eps,right-g1)
            if hi<=lo:continue
            # Width changes at pads may occupy the beginning/end of a segment.
            # Search inward only until a genuine separating section is found.
            for a in np.linspace(lo,min(hi,lo+max(2*self.guard,(hi-lo)*.15)),5):
                if self.prefix(a) is not None:break
            else:continue
            for b in np.linspace(hi,max(a,hi-max(2*self.guard,(hi-a)*.15)),5):
                part=self.partition(float(a),float(b))
                if part is not None:break
            else:continue
            candidates.append((float(b-a),float(a),float(b),part))
        if not candidates:
            # Bent or branched path: a topology-checked full-length interval.
            for b in np.linspace(self.fixed_start-eps,self.land_end+eps,24):
                part=self.partition(0.,float(b))
                if part is not None:candidates.append((float(b),0.,float(b),part));break
        if not candidates:return None
        self.candidate_intervals=tuple((q[1],q[2]) for q in sorted(candidates,reverse=True,key=lambda q:q[0]))
        # Sufficient bound on smoothstep shear; an insufficient length remains
        # explicitly warned, never passed off as a guaranteed safe region.
        safe=[q for q in candidates if q[0]>2.5*max_travel]
        best=max(safe or candidates,key=lambda q:q[0])
        self.nominal_first,self.nominal_second=best[1:3]
        if not safe:self.warning='动区长度小于全输入位移的保守过渡余量；需检查当前重构'
        return best[3]

    def edit(self,a,b,which,position):
        target=self.snap(position,which,b if which==0 else a)
        start=a if which==0 else b
        trials=[target,*np.linspace(target,start,25)[1:]]
        for value in trials:
            value=self.snap(float(value),which,b if which==0 else a)
            part=self.partition(value,b) if which==0 else self.partition(a,value)
            if part is not None:return part,abs(value-position)>max(self.tolerance*8,.01)
        return self.partition(a,b),True


    def edit_pose(self,a,b,which,position,center,state,style):
        """Independent source handle, with a physical-order stop in line mode."""
        part,clamped=self.edit(a,b,which,position)
        if part is None or style!='linear':return part,clamped
        from .connection_curves import linear_gap
        minimum=self.tolerance*16
        if linear_gap(part,self,center,state)>minimum:return part,clamped
        old=self.partition(a,b)
        if old is None or linear_gap(old,self,center,state)<=minimum:
            # A new extreme itself can reverse the endpoints. Retain the user's
            # interval and expose a disconnected red result; never invent a line.
            return part,True
        start=a if which==0 else b;target=part['a'] if which==0 else part['b']
        good=old;lo,hi=0.,1.
        for _ in range(16):
            t=(lo+hi)/2;probe,_=self.edit(a,b,which,start+t*(target-start))
            if probe is not None and linear_gap(probe,self,center,state)>minimum:lo=t;good=probe
            else:hi=t
        return good,True


def taper_mesh(part,plan,start,end):
    """Refine where the displacement weight changes, never a rigid landing.

    A very short transition used to subdivide the entire pad at span/48 and
    create tens of thousands of collinear points. Use the 48 transition samples
    on each intersecting source edge, leaving rigid regions and seams intact.
    """
    faces=[g.buffer(plan.tolerance*.05) for g in (part['near'],part['far']) if g.geom_type=='LineString']
    def ring(r):
        xy=np.asarray(r.coords);s=project(plan.route,xy);out=[]
        for p,q,u,v in zip(xy[:-1],xy[1:],s[:-1],s[1:]):
            out.append(p)
            if abs(v-u)<plan.tolerance*.01:continue
            segment=shapely.LineString([p,q])
            if any(face.covers(segment) for face in faces):continue
            ts=(np.linspace(start,end,49)-u)/(v-u)
            ts=np.sort(ts[(ts>1e-10)&(ts<1-1e-10)])
            out.extend(p+ts[:,None]*(q-p))
        out.append(xy[-1]);return out
    return shapely.union_all([shapely.Polygon(ring(p.exterior),[ring(h) for h in p.interiors]) for p in parts(part['A'])])


def _warp_variant(part,plan,center,state,style="c2",variant=0):
    """Move original material; C is rigid and A tapers continuously into B."""
    cancel_check()
    from .preview import rigid_transform
    if not np.any(state) and variant==0 and style in ('c1','c2'):return dict(A=part['A'],B=part['B'],C=part['C']),None
    memo_key=(style,variant,part_key(part),np.asarray(center,float).tobytes(),np.asarray(state,float).tobytes())
    if memo_key in plan.pose_memo:
        saved=plan.pose_memo[memo_key];plan.pose_memo.move_to_end(memo_key)
        return dict(saved[0]),saved[1]
    a,b=part['a'],part['b'];start=a if a>0 else plan.land_end
    end=plan.fixed_start if part['far'].geom_type=='Point' else b
    span=end-start
    if span<=plan.tolerance:return None,'动区过短'
    field_key=('field',style,part_key(part))
    def move(points):
        saved=plan.cache.get(field_key)
        if saved is None:
            s=project(plan.route,points);t=np.clip((s-start)/span,0,1)
            if any(abs(v)>1e-10 for v in part.get('angles',(0.,0.))):
                faces=[]
                for face,pos in ((part['near'],start),(part['far'],end)):
                    pivot=np.asarray(face.centroid.coords[0] if face.geom_type=='LineString' else plan.route.interpolate(pos).coords[0]);axis=plan.tangent(pos).copy()
                    if face.geom_type=='LineString':
                        edge=np.asarray(face.coords[-1])-np.asarray(face.coords[0]);axis=np.array([-edge[1],edge[0]]);axis/=max(np.linalg.norm(axis),1e-20)
                        if axis@plan.tangent(pos)<0:axis=-axis
                    faces.append((pivot,axis))
                left=(points-faces[0][0])@faces[0][1];right=(faces[1][0]-points)@faces[1][1]
                denominator=left+right
                if np.any(denominator<=plan.tolerance):raise ValueError('斜向边界在动区内交错')
                t=np.clip(left/denominator,0,1)
            w=1-(3*t**2-2*t**3) if style=="c1" else 1-(10*t**3-15*t**4+6*t**5)
            pt=shapely.points(points)
            if a>0:w=np.where(shapely.distance(pt,part['near'])<plan.tolerance*.1,1.,w)
            w=np.where(shapely.distance(pt,part['far'])<plan.tolerance*.1,0.,w)
            saved=points.copy(),w[:,None];plan.cache[field_key]=saved
        xy,weight=saved
        placed=rigid_transform(xy,center,state);out=xy+weight*(placed-xy)
        # Preserve the two seam coordinates bit-for-bit, including translations
        # near a half-DBU rounding boundary; avoid add/subtract cancellation.
        moved=(weight[:,0]==1);fixed=(weight[:,0]==0)
        out[moved]=placed[moved];out[fixed]=xy[fixed]
        return out
    try:
        if variant:
            from .turn_connection import turn_active
            active=turn_active(part,plan,center,state,style,variant)
        elif style in ('linear','arc'):
            from .connection_curves import endpoint_active
            active=endpoint_active(part,plan,center,state,style)
        else:
            key=('mesh',part_key(part));mesh=plan.cache.get(key)
            if mesh is None:mesh=taper_mesh(part,plan,start,end);plan.cache[key]=mesh
            active=shapely.transform(mesh,move)
    except (ValueError,shapely.errors.GEOSException) as exc:return None,str(exc)
    rigid_key=('rigid_mesh',part_key(part))
    rigid=plan.cache.get(rigid_key)
    if rigid is None:
        rigid=part['C'];plan.cache[rigid_key]=rigid
    translated=shapely.transform(rigid,lambda xy:rigid_transform(xy,center,state))
    # Floating-point seam slivers are repaired only within one source DBU.
    if not active.is_valid:
        repaired=shapely.make_valid(active)
        polygons=parts(repaired)
        if polygons:
            main=max(polygons,key=lambda g:g.area)
            scraps=[g for g in polygons if g is not main]
            def thin(g):
                rect=shapely.get_coordinates(shapely.minimum_rotated_rectangle(g))
                return len(rect)<4 or min(np.linalg.norm(np.diff(rect,axis=0),axis=1))<=plan.tolerance*2
            if all(thin(g) for g in scraps) and sum(g.area for g in scraps)<=plan.tolerance*active.length*2:
                active=main
    if active.is_empty:return None,'动区退化为空图形'
    if not active.is_valid:return None,'动区发生折叠：'+shapely.is_valid_reason(active)
    raw={'A':active,'B':part['B'],'C':translated}
    near=part['near'] if a==0 else shapely.transform(part['near'],lambda xy:rigid_transform(xy,center,state))
    seams=[near,part['far']]
    if part['near'].geom_type=='Point' or part['far'].geom_type=='Point':
        from .connection_curves import connection_data
        try:
            effective,_,_,_,_,_,_,_=connection_data(part,plan,center,state)
            seams.extend([shapely.transform(effective['near'],lambda xy:rigid_transform(xy,center,state)),effective['far']])
        except ValueError:pass
    seam=shapely.union_all(seams).buffer(plan.tolerance*2)
    allowance=max(plan.tolerance**2,plan.source.area*1e-10)
    for x,y in (('A','B'),('A','C'),('B','C')):
        overlap=raw[x].intersection(raw[y])
        outside=overlap.difference(seam)
        if outside.area>allowance or outside.length>plan.tolerance*4:return None,'动区与相邻区域在截面外相接或反向交叠'
    # Snap the JOINT copper once, rather than independently rounding two copies
    # of every seam. This removes sub-DBU gaps without creating new cut edges.
    full=shapely.set_precision(shapely.union_all(list(raw.values())),plan.tolerance)
    expected_holes=sum(len(q.interiors) for q in parts(plan.source))
    holes=[shapely.Polygon(r) for q in parts(full) for r in q.interiors]
    if len(holes)>expected_holes:
        repairs=[g for g in holes if g.area<=plan.tolerance*g.length and g.difference(seam).area<=plan.tolerance**2*.01]
        if repairs:full=shapely.union_all([full,*repairs])
    if len(parts(full))!=len(parts(plan.source)) or not full.is_valid or sum(len(q.interiors) for q in parts(full))!=expected_holes:
        return None,'重构连接不能闭合'
    fixed=shapely.intersection(full,shapely.set_precision(raw['B'],plan.tolerance))
    rigid=shapely.intersection(full,shapely.set_precision(raw['C'],plan.tolerance)).difference(fixed)
    result={'A':full.difference(shapely.union_all([fixed,rigid])),'B':fixed,'C':rigid}
    size=int(sum(shapely.get_num_coordinates(g) for g in result.values()))*24+512
    if size<=plan.memo_budget:
        plan.pose_memo[memo_key]=(dict(result),None,size);plan.memo_bytes+=size
        while plan.memo_bytes>plan.memo_budget or len(plan.pose_memo)>128:
            _,old=plan.pose_memo.popitem(last=False);plan.memo_bytes-=old[2]
    return result,None


def warp(part,plan,center,state,style="c2",variant=None):
    """Use the selected style; curved styles may make a verified return bend."""
    if variant is not None:return _warp_variant(part,plan,center,state,style,variant)
    key=('resolved-v40',style,part_key(part),np.asarray(center,float).tobytes(),np.asarray(state,float).tobytes())
    if key in plan.pose_memo:
        result,error,_=plan.pose_memo[key];plan.pose_memo.move_to_end(key)
        return None if result is None else dict(result),error
    result,error=_warp_variant(part,plan,center,state,style,0)
    if error and style!='linear':
        from .turn_connection import VARIANTS
        for v in range(1,len(VARIANTS)+1):
            cancel_check();alternate,reason=_warp_variant(part,plan,center,state,style,v)
            if reason is None:result,error=alternate,None;break
    size=(sum(int(shapely.get_num_coordinates(g))*24 for g in result.values()) if result is not None else 0)+512
    if size<=plan.memo_budget:
        plan.pose_memo[key]=(None if result is None else dict(result),error,size);plan.memo_bytes+=size
        while plan.memo_bytes>plan.memo_budget or len(plan.pose_memo)>128:
            _,old=plan.pose_memo.popitem(last=False);plan.memo_bytes-=old[2]
    return result,error


def failed_connection(part,plan,center,state):
    """Omit the failed connecting strip, retain real rigid terminal copper."""
    from .preview import rigid_transform
    moved=lambda g:shapely.transform(g,lambda xy:rigid_transform(xy,center,state))
    pads=[]
    if part['near'].geom_type=='Point':pads.append(moved(plan.land))
    if part['far'].geom_type=='Point':pads.extend(plan.anchors)
    try:
        from .connection_curves import connection_data
        _,landing,fixed_landing,*_=connection_data(part,plan,center,state)
        pads=[landing,fixed_landing]
    except ValueError:pass
    return dict(A=shapely.union_all(pads),B=part['B'],C=moved(part['C']))


def missing_area(part,plan,center,state):
    """A selectable warning band, not copper and never included in DXF."""
    from .preview import rigid_transform
    a=rigid_transform(np.asarray(part['near'].centroid.coords[0]),center,state)
    b=np.asarray(part['far'].centroid.coords[0])
    radius=max(part['near'].length,part['far'].length,plan.tolerance*16)/2
    if radius<=plan.tolerance*8:
        radius=max(plan.tolerance*8,min(20.,float(plan.land.area)**.5/3))
    return shapely.LineString([a,b]).buffer(radius,quad_segs=12)
