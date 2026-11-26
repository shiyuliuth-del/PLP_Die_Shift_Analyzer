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


def clean_overlay(g,tolerance):
    """Remove only sub-DBU floating overlay spurs, never edit imported source."""
    if g.is_empty:return g
    if g.geom_type=='Polygon' and not g.interiors and len(g.exterior.coords)<=5:return g
    epsilon=max(tolerance*1e-6,np.spacing(max(1.,max(abs(x) for x in g.bounds)))*32)
    simple=g.simplify(epsilon,preserve_topology=False)
    if not simple.is_empty and simple.is_valid and abs(simple.area-g.area)<=max(tolerance*tolerance*.01,epsilon*g.length):return simple
    return g


def part_key(part):
    return (part['a'],part['b'],*part.get('angles',(0.,0.)),part['strict_box'].wkb if part.get('strict_box') is not None else None)


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


def trace(poly,start,end,width,visibility_budget=None):
    """Shortest interior medial path, with exact source terminal endpoints."""
    # Prepared GEOS indexes are lazily mutated. Never attach one to a
    # source polygon shared by foreground and background reconstruction.
    poly=shapely.from_wkb(poly.wkb)
    endpoints=shapely.MultiPoint([start,end])
    # A path certified by exact covers cannot begin outside its domain or
    # cross disconnected components. Reject before constructing large graphs.
    if not poly.covers(endpoints):return None
    if poly.geom_type=='MultiPolygon':
        poly=next((g for g in poly.geoms if g.covers(endpoints)),None)
        if poly is None:return None
    shapely.prepare(poly)
    start,end=np.asarray(start,float),np.asarray(end,float)
    if np.linalg.norm(end-start)<1e-8:return None
    direct=shapely.LineString([start,end])
    if poly.covers(direct):return direct
    nodes,base=medial_graph(poly.wkb,round(float(width),6))
    if not len(nodes):
        direct=shapely.LineString([start,end])
        if poly.covers(direct):return direct
        from .reconstruction_visibility import trace as fallback
        return fallback(poly,start,end,width,max_edges=visibility_budget)
    active=np.array([i for i,row in enumerate(base) if row],int)
    if not len(active):
        from .reconstruction_visibility import trace as fallback
        return fallback(poly,start,end,width,max_edges=visibility_budget)
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
    if dst not in previous:
        from .reconstruction_visibility import trace as fallback
        return fallback(poly,start,end,width,max_edges=visibility_budget)
    order=[dst]
    while order[-1]!=src:order.append(previous[order[-1]])
    line=shapely.LineString(allxy[order[::-1]])
    # Remove numerical teeth of the discretised medial axis, without crossing
    # holes or leaving source copper. A rejected simplification retains the graph.
    simple=line.simplify(max(width*.07,1e-4),preserve_topology=False)
    return simple if poly.covers(simple) else line


def project(route,points):
    return shapely.line_locate_point(route,shapely.points(np.asarray(points,float)))


def terminal_marks(route,geometry):
    """Arclengths where the actual trace crosses a terminal, not remote vertices."""
    crossing=route.intersection(geometry)
    points=shapely.get_coordinates(crossing)
    return project(route,points) if len(points) else np.empty(0)


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
        first=terminal_marks(route,land);last=terminal_marks(route,anchor)
        if not len(first) or not len(last):return None
        lo=float(np.max(first))+max(tolerance*16,.01)
        hi=float(np.min(last))-max(tolerance*16,.01)
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
        trunk=clean_overlay(source.difference(shapely.union_all(distal)),tolerance)
        if trunk.is_empty or land.difference(trunk).area>epsilon:return None
        result=[(clean_overlay(source.difference(shapely.union_all([other for j,other in enumerate(distal) if j!=i])),tolerance),trunk,anchor) for i,anchor in enumerate(anchors)]
        if trunk.geom_type!='Polygon' or any(g.geom_type!='Polygon' or not g.is_valid for g,_,_ in result):bad.update(range(len(distal)))
        if not bad:
            if shapely.union_all([g for g,_,_ in result]).symmetric_difference(source).area>epsilon:return None
            return [(clean_overlay(g,tolerance),clean_overlay(trunk,tolerance),anchor) for g,trunk,anchor in result]
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
    cut_mode:str='auto'
    cache:dict=field(default_factory=dict,repr=False)

    def __post_init__(self):
        self.pose_memo=OrderedDict();self.memo_bytes=0
        self.xy=np.asarray(self.route.coords)
        self.lengths=np.linalg.norm(np.diff(self.xy,axis=0),axis=1)
        self.marks=np.r_[0,np.cumsum(self.lengths)]
        self.length=float(self.route.length)
        self.identity=hashlib.sha256(self.route.wkb).hexdigest()[:20]
        moving_marks=terminal_marks(self.route,self.land)
        self.land_end=float(moving_marks.max()) if len(moving_marks) else 0.
        fixed_marks=[terminal_marks(self.route,g) for g in self.anchors]
        self.fixed_start=min((float(v.min()) for v in fixed_marks if len(v)),default=self.length)
        self.fixed_start=max(0.,min(self.fixed_start,self.length))
        self.corners=[]
        for i in range(1,len(self.xy)-1):
            u=(self.xy[i]-self.xy[i-1])/self.lengths[i-1];v=(self.xy[i+1]-self.xy[i])/self.lengths[i]
            if float(u@v)<math.cos(math.radians(8)):
                radius=min(max(self.guard,float(shapely.Point(self.xy[i]).distance(self.source.boundary))*.65),.3*min(self.lengths[i-1],self.lengths[i]))
                self.corners.append((float(self.marks[i]),radius))

    def _moving_cover(self):
        """Physical moving terminals (vias), the hard requirement for the C side."""
        tv=getattr(self,'terminal_vias',None)
        cached=getattr(self,'_moving_cover_cache',None)
        if cached is not None and cached[0] is tv:return cached[1],cached[2],cached[3]
        geom=self.land
        if tv and tv[0]:
            polys=[g for g in tv[0] if g is not None and not g.is_empty]
            if polys:geom=shapely.union_all(polys) if len(polys)>1 else polys[0]
        self._moving_cover_cache=(tv,geom,float(geom.area),geom.bounds)
        return self._moving_cover_cache[1:]

    def via_limits(self):
        """Cut limits tangent to the actual terminal vias, not the enclosing pad.

        `land_end`/`fixed_start` stop at the pad+annulus boundary. The user may
        move a boundary until it is tangent to the via itself; the annulus ring
        between that tangent and the pad edge belongs to the transition. The
        physical vias still stay entirely on their own rigid side, which
        `prefix` verifies against these same polygons.
        """
        tv=getattr(self,'terminal_vias',None)
        cached=getattr(self,'_via_limits_cache',None)
        if cached is not None and cached[0] is tv:return cached[1],cached[2]
        lo,hi=self.land_end,self.fixed_start
        if tv:
            moving=[]
            for g in (tv[0] or ()):
                if g is None or g.is_empty:continue
                marks=terminal_marks(self.route,g)
                if len(marks):moving.append(float(marks.max()))
            fixed=[]
            for g in (tv[1] or ()):
                if g is None or g.is_empty:continue
                marks=terminal_marks(self.route,g)
                if len(marks):fixed.append(float(marks.min()))
            if moving:lo=min(self.land_end,min(moving))
            if fixed:hi=max(self.fixed_start,min(fixed))
        if hi<lo:hi=lo
        self._via_limits_cache=(tv,lo,hi)
        return lo,hi

    def __getstate__(self):
        state=dict(self.__dict__);state['cache']={};state['pose_memo']=OrderedDict();state['memo_bytes']=0
        for name in ('_ring_cache','_land_cache','_anchor_cache','_source_meta','_side_meta','_via_limits_cache','_moving_cover_cache'):state.pop(name,None)
        return state

    def _source_info(self):
        meta=getattr(self,'_source_meta',None)
        if meta is not None and meta[0] is self.source:return meta
        rings=[np.asarray(r.coords,float) for p in parts(self.source) for r in (p.exterior,*p.interiors)]
        if rings:
            starts=np.concatenate([r[:-1] for r in rings])
            ends=np.concatenate([r[1:] for r in rings])
        else:
            starts=ends=np.empty((0,2),float)
        meta=(self.source,self.source.bounds,float(self.source.area),starts,ends)
        self._source_meta=meta
        return meta

    def _land_info(self):
        info=getattr(self,'_land_cache',None)
        if info is not None and info[0] is self.land:return info[1],info[2]
        area=float(self.land.area);bounds=self.land.bounds
        self._land_cache=(self.land,area,bounds)
        return area,bounds

    def _anchor_info(self):
        info=getattr(self,'_anchor_cache',None)
        if info is not None and info[0] is self.anchors:return info[1]
        bounds=tuple(a.bounds for a in self.anchors)
        self._anchor_cache=(self.anchors,bounds)
        return bounds

    def _point_at(self,s):
        if s<=0.:return self.xy[0]
        if s>=self.length:return self.xy[-1]
        i=min(len(self.lengths)-1,max(0,int(np.searchsorted(self.marks,s,side='right')-1)))
        length=self.lengths[i]
        if length<=1e-12:return self.xy[i]
        return self.xy[i]+((s-self.marks[i])/length)*(self.xy[i+1]-self.xy[i])

    @staticmethod
    def _bounds_overlap(a,b):
        return a[0]<=b[2] and a[2]>=b[0] and a[1]<=b[3] and a[3]>=b[1]

    @property
    def key(self):return self.layer,self.index,self.owner,self.refs

    def tangent(self,s):
        i=min(len(self.lengths)-1,max(0,int(np.searchsorted(self.marks,s,side='right')-1)))
        return (self.xy[i+1]-self.xy[i])/max(self.lengths[i],1e-12)

    @property
    def allows_pad_point(self):
        return getattr(self,'pad_modes',(True,False))[0]

    def pad_hit(self,cursor,which):
        if which!=0 or not self.allows_pad_point:return False
        if cursor is None:return True
        terminals=getattr(self,'terminal_vias',None)
        if terminals is None:return True
        values=terminals[which]
        if not values:return False
        origin=shapely.Point(self.route.coords[0 if which==0 else -1]);point=shapely.Point(cursor)
        # The hit area is the landing, not just the via plus corner guard.
        # Enclosure often exceeds guard+trigger, which made a legitimate pad
        # unreachable. Bind to this path's terminal, never another branch pad.
        nearest=min(g.distance(origin) for g in values)
        targets=[g for g in values if g.distance(origin)<=nearest+self.tolerance]
        other=[g for g in values if all(g is not t for t in targets)]
        if other and min(g.distance(point) for g in other)+self.tolerance<min(g.distance(point) for g in targets):return False
        landing=self.land if which==0 else shapely.union_all([a for a in self.anchors if any(a.intersects(g) for g in targets)])
        return not landing.is_empty and landing.buffer(self.tolerance*4).covers(point)

    def drag_projection(self,cursor,previous,previous_cursor):
        """Continuous local arclength projection; close return legs cannot steal it."""
        cursor=np.asarray(cursor,float);travel=float(np.linalg.norm(cursor-np.asarray(previous_cursor,float)))
        if travel<=self.tolerance*1e-6:return float(previous)
        reach=max(travel*3,self.guard*2,self.tolerance*32)
        lo=max(0.,previous-reach);hi=min(self.length,previous+reach)
        indices=np.flatnonzero((self.marks[:-1]<=hi)&(self.marks[1:]>=lo))
        best=None
        for i in indices:
            length=self.lengths[i]
            if length<=1e-12:continue
            unit=(self.xy[i+1]-self.xy[i])/length
            s=float(np.clip(self.marks[i]+(cursor-self.xy[i])@unit,max(lo,self.marks[i]),min(hi,self.marks[i+1])))
            projected=self.xy[i]+(s-self.marks[i])*unit
            score=(float(np.linalg.norm(cursor-projected)),abs(s-previous))
            if best is None or score<best[0]:best=(score,s)
        return float(previous) if best is None else best[1]

    def snap(self,s,which,other,allow_pad=True,corner_snap=None):
        eps=max(self.tolerance*8,1e-4);cut_lo,cut_hi=self.via_limits()
        lo=0. if which==0 else max(other+eps,cut_lo+eps)
        hi=min(other-eps,cut_hi-eps) if which==0 else cut_hi-eps
        if hi<lo:return lo
        s=float(np.clip(s,lo,hi))
        margin=getattr(self,'pad_trigger',0.)
        if which==0 and s<=self.land_end+eps:
            # Grabbing the pad itself still collapses the boundary to a point
            # when that mode is enabled. Otherwise the closest straight cut is
            # tangent to the via, leaving only the pad annulus in the transition.
            if allow_pad and self.allows_pad_point:
                if s<=max(0.,self.land_end-margin):return 0.
                return min(hi,max(lo,cut_lo))
            return min(hi,max(lo,cut_lo))
        for corner,radius in self.corners:
            if corner_snap is not None:
                if corner_snap<=0:break
                segment=int(np.searchsorted(self.marks,corner))
                radius=min(corner_snap,.3*min(self.lengths[segment-1:segment+1]))
            if corner-radius<s<corner+radius:
                options=[x for x in (corner-radius,corner+radius) if lo<=x<=hi]
                if options:s=min(options,key=lambda x:abs(x-s))
                else:s=lo if which==0 else hi
        return s

    def section(self,s,angle=0.):
        key=('section',round(float(s),8),round(float(angle),10))
        if key in self.cache:return self.cache[key]
        point=self._point_at(float(s));normal=self.tangent(s);u=np.array([-normal[1],normal[0]])
        if angle:
            co,si=np.cos(angle),np.sin(angle);u=np.array([co*u[0]-si*u[1],si*u[0]+co*u[1]])
        _,bounds,_,starts,ends=self._source_info()
        result=None;exact=True
        if len(starts):
            perp=np.array([-u[1],u[0]])
            d0=(starts-point)@perp;d1=(ends-point)@perp
            eps=max(self.tolerance*1e-4,1e-9)
            if not np.any((np.abs(d0)<=eps)|(np.abs(d1)<=eps)):
                cross=np.flatnonzero((d0<0)!=(d1<0))
                if not len(cross):
                    exact=False;result=None
                elif (len(cross)&1)==0:
                    alpha=d0[cross]/(d0[cross]-d1[cross])
                    hits=starts[cross]+alpha[:,None]*(ends[cross]-starts[cross])
                    ts=np.sort((hits-point)@u)
                    best=None
                    for lo,hi in zip(ts[0::2],ts[1::2]):
                        if hi-lo<=self.tolerance:continue
                        dist=0. if lo<=0.<=hi else (lo if lo>0. else -hi)
                        if best is None or dist<best[0]:best=(dist,float(lo),float(hi))
                    exact=False
                    result=shapely.LineString([point+best[1]*u,point+best[2]*u]) if best is not None else None
        if exact:
            size=np.hypot(bounds[2]-bounds[0],bounds[3]-bounds[1])+1
            chord=shapely.intersection(self.source,shapely.LineString([point-u*size,point+u*size]))
            lines=[q for q in shapely.get_parts(chord) if q.geom_type=='LineString' and q.length>self.tolerance]
            result=min(lines,key=lambda g:float(g.distance(shapely.Point(point)))) if lines else None
        if len(self.cache)>4096:
            for old_key in list(self.cache)[:1024]:del self.cache[old_key]
        self.cache[key]=result
        return result

    def prefix(self,s,angle=0.,*,tight=None):
        """Connected source portion on the moving-terminal side of a local cut."""
        if tight is None:tight=getattr(self,'cut_mode','auto')=='manual'
        key=('prefix',round(float(s),8),round(float(angle),10),bool(tight))
        if key in self.cache:return self.cache[key]
        face=self.section(s,angle)
        if face is None:return None
        xy=np.asarray(face.coords);v=xy[-1]-xy[0];v/=max(np.linalg.norm(v),1e-12)
        blade=shapely.LineString([xy[0]-v*self.tolerance*16,xy[-1]+v*self.tolerance*16])
        try:pieces=parts(split(self.source,blade))
        except (ValueError,shapely.errors.GEOSException):return None
        land_area,land_bounds=self._land_info();anchor_bounds=self._anchor_info()
        if tight:target,cover_area,cover_bounds=self._moving_cover()
        else:target,cover_area,cover_bounds=self.land,land_area,land_bounds
        eps=max(self.tolerance**2,land_area*1e-7)
        candidates=[g for g in pieces if self._bounds_overlap(g.bounds,cover_bounds) and (g.covers(target) or g.intersection(target).area>=cover_area-eps)]
        tv=getattr(self,'terminal_vias',None)
        if tight and tv and tv[1]:
            # The transition may cut through the fixed pad annulus, but never
            # through a fixed via: exclude only the actual fixed via bodies.
            fixed_targets=[g for g in tv[1] if g is not None and not g.is_empty]
            fixed_bounds=[g.bounds for g in fixed_targets]
        else:
            fixed_targets=list(self.anchors);fixed_bounds=list(anchor_bounds)
        candidates=[g for g in candidates if all(not self._bounds_overlap(g.bounds,ab) or not g.intersects(a) or g.intersection(a).area<=eps for a,ab in zip(fixed_targets,fixed_bounds))]
        extra_fixed=getattr(self,'cluster_fixed',None)
        if candidates and extra_fixed is not None and not extra_fixed.is_empty:
            feps=max(self.tolerance**2,1e-8)
            candidates=[g for g in candidates if g.intersection(extra_fixed).area<=feps]
        extra_moving=getattr(self,'cluster_moving',None)
        if candidates and extra_moving is not None and not extra_moving.is_empty:
            meps=max(self.tolerance**2,1e-8)
            candidates=[g for g in candidates if extra_moving.difference(g).area<=meps]
        if len(pieces)<2 or len(candidates)!=1:result=None
        else:result=candidates[0],face,shapely.union_all([g for g in pieces if g is not candidates[0]])
        if len(self.cache)>4096:
            for old_key in list(self.cache)[:1024]:del self.cache[old_key]
        self.cache[key]=result
        return result

    def fixed_side(self,s):
        # Convert legacy saved whole-fixed paths to the last real line cut.
        return min(float(s),self.via_limits()[1]-max(self.tolerance*8,1e-4))

    def moving_line_partition(self,b,angles=(0.,0.),*,tight=None):
        """Find the first usable straight moving-side section beyond the pad."""
        if tight is None:tight=getattr(self,'cut_mode','auto')=='manual'
        b=self.fixed_side(b)
        angles=tuple(map(float,angles))
        mkey=('moving-line',round(b,8),angles,bool(tight))
        if mkey in self.cache:
            cached=self.cache[mkey]
            return None if cached is None else dict(cached)
        step=max(self.tolerance*8,1e-4)
        lo=(self.via_limits()[0] if tight else self.land_end)+step
        hi=b-step
        if hi<lo or self.prefix(b,angles[1],tight=tight) is None:
            self.cache[mkey]=None;return None
        part=self.partition(lo,b,angles,tight=tight)
        if part is not None:
            self.cache[mkey]=dict(part);return part
        # Include bend limits so a usable neck is not skipped by uniform probes.
        ends=[corner+guard for corner,guard in self.corners if lo<corner+guard<hi]
        stations=sorted(set([*np.linspace(lo,hi,17)[1:],*ends]))
        previous=lo
        for pos in stations:
            cancel_check()
            part=self.partition(pos,b,angles)
            if part is None:
                previous=pos
                continue
            while pos-previous>step:
                mid=(previous+pos)/2
                candidate=self.partition(mid,b,angles)
                if candidate is None:previous=mid
                else:pos=mid;part=candidate
            self.cache[mkey]=dict(part);return part
        self.cache[mkey]=None
        return None

    def partition(self,a,b,angles=(0.,0.),*,tight=None):
        """Return disjoint A/B/C and their real shared boundaries, or no partition."""
        if tight is None:tight=getattr(self,'cut_mode','auto')=='manual'
        b=self.fixed_side(b)
        if a>=b:return None
        if a==0 and not self.allows_pad_point:return self.moving_line_partition(b,angles,tight=tight)
        angles=tuple(map(float,angles))
        key=('partition',float(a),b,angles,bool(tight))
        cached=self.cache.get(key)
        if cached is not None:return dict(cached)
        outer=self.prefix(b,angles[1],tight=tight)
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
            land_area,land_bounds=self._land_info()
            if tight:target,cover_area,cover_bounds=self._moving_cover()
            else:target,cover_area,cover_bounds=self.land,land_area,land_bounds
            epsilon=max(self.tolerance**2,land_area*1e-7)
            movable=[g for g in pieces if self._bounds_overlap(g.bounds,cover_bounds) and (g.covers(target) or g.intersection(target).area>=cover_area-epsilon)]
            extra_moving=getattr(self,'cluster_moving',None)
            if len(movable)==1 and extra_moving is not None and not extra_moving.is_empty:
                meps=max(self.tolerance**2,1e-8)
                if extra_moving.difference(movable[0]).area>meps:movable=[]
            if len(movable)!=1 or len(pieces)<2:return None
            c=movable[0];a_shape=shapely.union_all([g for g in pieces if g is not c])
        if a_shape.is_empty:return None
        _,_,source_area,_,_=self._source_info()
        area_eps=max(self.tolerance**2,source_area*1e-8)
        diff=abs((a_shape.area+b_shape.area+c.area)-source_area)
        if diff>area_eps:return None
        if diff>area_eps*.1 or not (a_shape.is_valid and b_shape.is_valid and (c.is_empty or c.is_valid)):
            union=shapely.union_all([a_shape,b_shape,c])
            if union.symmetric_difference(self.source).area>area_eps:return None
        a_shape,b_shape,c=(clean_overlay(g,self.tolerance) for g in (a_shape,b_shape,c))
        result={'A':a_shape,'B':b_shape,'C':c,'near':near,'far':far,'a':a,'b':b,'angles':angles}
        if len(self.cache)>4096:
            for old_key in list(self.cache)[:1024]:del self.cache[old_key]
        self.cache[key]=dict(result)
        return result

    def pad_handle(self,side):
        """UI-only branchward point; electrical endpoint and export stay exact."""
        origin=np.asarray(self.route.coords[0 if side==0 else -1],float)
        cut_lo,cut_hi=self.via_limits()
        position=min(self.length,max(cut_lo+self.guard,self.length*.25)) if side==0 else max(0.,min(cut_hi-self.guard,self.length*.75))
        toward=np.asarray(self.route.interpolate(position).coords[0])-origin
        pad=self.land if side==0 else min(self.anchors,key=lambda g:g.distance(shapely.Point(origin)),default=self.source)
        radius=float(shapely.Point(origin).distance(pad.boundary))
        length=np.linalg.norm(toward)
        if length<self.tolerance:return shapely.Point(origin)
        return shapely.Point(origin+toward/length*min(radius*.45,length*.2,20.))

    def width_variation(self,a,b):
        key=('width-profile',float(a),float(b))
        if key in self.cache:return self.cache[key]
        sections=[self.section(float(t)) for t in np.linspace(a,b,9)]
        widths=[g.length for g in sections if g is not None]
        if len(widths)!=9:return float('inf')
        value=(max(widths)-min(widths))/max(min(widths),self.tolerance)
        self.cache[key]=value;return value

    def choose(self,max_travel):
        """Longest straight candidate; motion envelope determines its margin.

        This is a bounded engineering search, not a global routing optimum.
        The same full-input displacement bound is used in every pose.
        """
        self.cut_mode='auto'
        preferred=getattr(self,'preferred_land',None)
        if preferred is not None and preferred.difference(self.land).area>self.tolerance**2:
            original_land=self.land;original_end=self.land_end
            self.land=preferred
            marks=terminal_marks(self.route,preferred);self.land_end=float(marks.max()) if len(marks) else original_end
            self.preferred_land=None;self.cache.clear()
            try:chosen=self.choose(max_travel)
            finally:
                self.land=original_land;self.land_end=original_end;self.cache.clear();self.preferred_land=preferred
            if chosen is not None:return chosen
        eps=max(self.tolerance*16,.01)
        auto_end=min(self.fixed_start,getattr(self,'default_fixed_start',self.fixed_start))
        breaks=[(self.land_end+eps,0.),*(c for c in self.corners if self.land_end+eps<c[0]<auto_end-eps),(auto_end-eps,0.)]
        candidates=[]
        for (left,g0),(right,g1) in zip(breaks,breaks[1:]):
            lo=max(self.land_end+eps,left+g0);hi=min(auto_end-eps,right-g1)
            if hi<=lo:continue
            # Width changes at pads may occupy the beginning/end of a segment.
            # Search inward only until a genuine separating section is found.
            for a in np.linspace(lo,min(hi,lo+max(2*self.guard,(hi-lo)*.15)),5):
                if self.prefix(a,tight=False) is not None:break
            else:continue
            for b in np.linspace(hi,max(a,hi-max(2*self.guard,(hi-a)*.15)),5):
                part=self.partition(float(a),float(b),tight=False)
                if part is not None:break
            else:continue
            candidates.append((float(b-a),float(a),float(b),part))
        if not candidates:
            # Bent or branched path: a topology-checked full-length interval.
            for b in np.linspace(auto_end-eps,self.land_end+eps,24):
                part=self.partition(0.,float(b),tight=False)
                if part is not None:
                    candidates.append((part['b']-part['a'],part['a'],part['b'],part));break
        if not candidates:return None
        # A long straight centreline may begin inside a wide junction/pad.
        # Search its interior for a constant-width run instead of ranking the
        # whole segment (which can hide a perfectly usable uniform section).
        uniform=[];best_uniform=0.
        for length,a,b,_ in sorted(candidates,key=lambda q:-q[0]):
            if length<=best_uniform:continue
            if self.width_variation(a,b)<=.05:
                best_uniform=max(best_uniform,length);continue
            stations=np.linspace(a,b,25)
            widths=[self.section(float(t)) for t in stations]
            widths=[g.length if g is not None else float('nan') for g in widths]
            best=None
            for left in range(len(stations)-1):
                if stations[-1]-stations[left]<=best_uniform:break
                for right in range(left+1,len(stations)):
                    w=widths[left:right+1]
                    if not np.isfinite(w).all() or min(w)<=self.tolerance or max(w)>min(w)*1.05:break
                    if best is None or stations[right]-stations[left]>stations[best[1]]-stations[best[0]]:best=(left,right)
            if best and stations[best[1]]-stations[best[0]]>best_uniform:
                lo,hi=map(float,stations[list(best)])
                part=self.partition(lo,hi,tight=False)
                if part is not None:
                    uniform.append((hi-lo,lo,hi,part))
                    best_uniform=max(best_uniform,hi-lo)
        candidates.extend(uniform)
        def rank(q):
            variation=self.width_variation(q[1],q[2])
            return (variation>.05,variation if variation>.05 else 0.,-q[0])
        candidates.sort(key=rank)
        self.candidate_intervals=tuple((q[1],q[2]) for q in candidates)
        # Sufficient bound on smoothstep shear; an insufficient length remains
        # explicitly warned, never passed off as a guaranteed safe region.
        safe=[q for q in candidates if q[0]>2.5*max_travel]
        pool=safe or [q for q in candidates if q[0]>=max_travel] or candidates
        best=min(pool,key=rank)
        self.nominal_first,self.nominal_second=best[1:3]
        if not safe:self.warning='动区长度小于全输入位移的保守过渡余量；需检查当前重构'
        return best[3]

    def edit(self,a,b,which,position,allow_pad=True,corner_snap=None):
        self.cut_mode='manual'
        target=self.snap(position,which,b if which==0 else a,allow_pad,corner_snap)
        start=a if which==0 else b
        trials=[target,*np.linspace(target,start,25)[1:]]
        for value in trials:
            value=self.snap(float(value),which,b if which==0 else a,allow_pad,corner_snap)
            part=self.partition(value,b) if which==0 else self.partition(a,value)
            if part is not None:return part,abs(value-position)>max(self.tolerance*8,.01)
        return self.partition(a,b),True


    def edit_pose(self,a,b,which,position,center,state,style,cursor=None,corner_snap=None):
        """Independent source handle, with a physical-order stop in line mode."""
        part,clamped=self.edit(a,b,which,position,self.pad_hit(cursor,which),corner_snap)
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
            t=(lo+hi)/2;probe,_=self.edit(a,b,which,start+t*(target-start),corner_snap=corner_snap)
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


def _warp_geometry(part,plan,center,state,style="c2",variant=0):
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
        if variant<0:
            from .parallel_curves import offset_active
            active=offset_active(part,plan,center,state,style,(-variant+1)/2)
        elif variant:
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
    translated=clean_overlay(shapely.transform(rigid,lambda xy:rigid_transform(xy,center,state)),plan.tolerance)
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
    seam=None
    allowance=max(plan.tolerance**2,plan.source.area*1e-10)
    for x,y in (('A','B'),('A','C'),('B','C')):
        gx,gy=raw[x],raw[y]
        if gx.is_empty or gy.is_empty:continue
        bx,by=gx.bounds,gy.bounds
        if bx[0]>by[2] or bx[2]<by[0] or bx[1]>by[3] or bx[3]<by[1]:continue
        overlap=gx.intersection(gy)
        if overlap.is_empty or (overlap.area<=allowance and overlap.length<=plan.tolerance*4):continue
        if seam is None:
            near=part['near'] if a==0 else shapely.transform(part['near'],lambda xy:rigid_transform(xy,center,state))
            seams=[near,part['far']]
            if part['near'].geom_type=='Point' or part['far'].geom_type=='Point':
                from .connection_curves import connection_data
                try:
                    effective,_,_,_,_,_,_,_=connection_data(part,plan,center,state)
                    seams.extend([shapely.transform(effective['near'],lambda xy:rigid_transform(xy,center,state)),effective['far']])
                except ValueError:pass
            seam=shapely.union_all(seams).buffer(plan.tolerance*2)
        outside=overlap.difference(seam)
        if outside.area>allowance or outside.length>plan.tolerance*4:return None,'动区与相邻区域在截面外相接或反向交叠'
    # Snap the JOINT copper once, rather than independently rounding two copies
    # of every seam. This removes sub-DBU gaps without creating new cut edges.
    full=shapely.set_precision(shapely.union_all(list(raw.values())),plan.tolerance)
    expected_holes=sum(len(q.interiors) for q in parts(plan.source))
    holes=[shapely.Polygon(r) for q in parts(full) for r in q.interiors]
    if len(holes)>expected_holes:
        if seam is None:
            near=part['near'] if a==0 else shapely.transform(part['near'],lambda xy:rigid_transform(xy,center,state))
            seam=shapely.union_all([near,part['far']]).buffer(plan.tolerance*2)
        repairs=[g for g in holes if g.area<=plan.tolerance*g.length and g.difference(seam).area<=plan.tolerance**2*.01]
        if repairs:full=shapely.union_all([full,*repairs])
    if len(parts(full))!=len(parts(plan.source)) or not full.is_valid or sum(len(q.interiors) for q in parts(full))!=expected_holes:
        return None,'重构连接不能闭合'
    def _polygonal(g):
        ps=parts(g)
        return ps[0] if len(ps)==1 else shapely.union_all(ps) if ps else EMPTY
    fixed=_polygonal(shapely.intersection(full,shapely.set_precision(raw['B'],plan.tolerance)))
    rigid=_polygonal(_polygonal(shapely.intersection(full,shapely.set_precision(raw['C'],plan.tolerance))).difference(fixed))
    active_final=_polygonal(full.difference(shapely.union_all([fixed,rigid])))
    if active_final.is_empty:return None,'动区退化为空图形'
    result={'A':active_final,'B':fixed,'C':rigid}
    size=int(sum(shapely.get_num_coordinates(g) for g in result.values()))*24+512
    if size<=plan.memo_budget:
        plan.pose_memo[memo_key]=(dict(result),None,size);plan.memo_bytes+=size
        while plan.memo_bytes>plan.memo_budget or len(plan.pose_memo)>128:
            _,old=plan.pose_memo.popitem(last=False);plan.memo_bytes-=old[2]
    return result,None


def _warp_variant(part,plan,center,state,style="c2",variant=0):
    try:return _warp_geometry(part,plan,center,state,style,variant)
    except (ValueError,shapely.errors.GEOSException) as exc:
        return None,'连接几何无法通过布尔校验：'+str(exc)


def warp(part,plan,center,state,style="c2",variant=None):
    """Use the selected style; curved styles may make a verified return bend."""
    if variant is not None:return _warp_variant(part,plan,center,state,style,variant)
    key=('resolved-v41',style,part_key(part),np.asarray(center,float).tobytes(),np.asarray(state,float).tobytes())
    if key in plan.pose_memo:
        result,error,_=plan.pose_memo[key];plan.pose_memo.move_to_end(key)
        return None if result is None else dict(result),error
    result,error=_warp_variant(part,plan,center,state,style,0)
    if error and style!='linear':
        from .turn_connection import VARIANTS
        for v in (-1,*range(1,len(VARIANTS)+1)):
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
    moved=lambda g:clean_overlay(shapely.transform(clean_overlay(g,plan.tolerance),lambda xy:rigid_transform(xy,center,state)),plan.tolerance)
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
