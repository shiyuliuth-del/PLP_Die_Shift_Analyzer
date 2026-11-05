"""Capacity of nominal route crossings between landing pads, fixed islands and the package.

Lengths are in micrometres. A gate retains its nominal demand and source polygon
references at every pose. The nominal catalogue is built once per engine.
"""
from collections import defaultdict
from dataclasses import dataclass,replace
import math
import threading
import numpy as np
import shapely
from shapely.geometry import LineString, Point, Polygon
from shapely.strtree import STRtree
from .drc_rules import effective_rules
from .session_cache import SessionCache,EphemeralCache
from .resources import parallel_map,resource_profile,geometry_budget
from .stack import UnionFind
from .neighbor_channels import occupied_batches, local_openings
from .landing_geometry import embedded_pad

ADEQUATE, NARROW, BLOCKED = '宽度足够','需局部收窄','宽度不足'
COLORS = {BLOCKED:'#c44956', NARROW:'#bd8127', ADEQUATE:'#498d79'}
SCOPE = '名义固定 RDL 有线路穿越的相邻实体局部窄口。矩形沿该对实体的实际最短截面，普通壁及封装侧保留线距，禁布区可贴边但不可进入。'
CHUNK = 256


def capacity(gap, width, space, margins=None):
    """Largest n >= 0 with n*w + (n-1)*s + left + right <= gap."""
    if margins is None:margins=(space,space)
    if min(width, space) <= 0:
        raise ValueError('通道的有效线宽和线距必须大于零')
    # Allow floating-point roundoff at an exact design-rule equality.
    ratio = (gap - sum(margins) + space) / (width + space)
    return max(0, math.floor(ratio + 8 * math.ulp(max(1., abs(ratio)))))


def holds_original(slots, widths, space):
    """Greedily pack contiguous runs in strand order, leaving both wall margins."""
    index = 0
    for gap in slots:
        used = space
        epsilon = 8*math.ulp(max(1.,gap))
        while index<len(widths) and used+widths[index]+space<=gap+epsilon:
            used += widths[index]+space
            index += 1
    return index==len(widths)


def gate_rectangle(row, width, space):
    if 'witness_um' in row:
        a,b=np.asarray(row['witness_um'])
        center=(a+b)/2
    else:
        center=np.asarray(row['point'])
    d = np.asarray(row['direction'])
    half = d*max(width*.025,row['gap_um']/2)
    side = np.array([-d[1],d[0]])*(width+space)*.65
    return Polygon([center-half-side,center+half-side,center+half+side,center-half+side])


@dataclass
class Wall:
    name: str
    kind: str
    geometry: object
    refs: tuple
    owner: int = -1
    layers: tuple = ()
    net: int | None = None
    contacts: frozenset = frozenset()
    pads: tuple = ()


@dataclass
class Gate:
    pair: tuple
    features: tuple
    ends: np.ndarray
    anchored: bool
    widths: tuple
    positions: tuple
    strand_refs: tuple
    nominal_gap: float
    band: tuple | None = None


def section_subset(plane,ids,wall_ids):
    """Compact indices keep a local solve proportional to participating geometry."""
    walls=plane['walls'];wall_ids=sorted(wall_ids)
    wall_map={v:i for i,v in enumerate(wall_ids)}
    features=sorted({f for k in ids for f in plane['gates'][k].features})
    fmap={v:i for i,v in enumerate(features)}
    gates=[replace(plane['gates'][k],pair=tuple(wall_map[i] for i in plane['gates'][k].pair),
                   features=tuple(fmap[i] for i in plane['gates'][k].features)) for k in ids]
    return dict(gates=gates,walls=[walls[i] for i in wall_ids],keys=plane['keys'],
                metal_count=sum(i<plane['metal_count'] for i in wall_ids),pieces=plane['pieces'][features],
                parents=np.array([wall_map[plane['parents'][i]] for i in features],int),local=plane['local'][features])


def _faces(geometry):
    """Index parallel boundary segments once per wall for tied-distance sweeps."""
    rings = ([geometry] if geometry.geom_type=='LineString' else [geometry.exterior,*geometry.interiors])
    result = defaultdict(list)
    for ring in rings:
        xy = shapely.get_coordinates(ring)
        delta = np.diff(xy,axis=0)
        angles = np.mod(np.arctan2(delta[:,1],delta[:,0]),np.pi)
        for j,key in enumerate(np.round(angles*1.e6).astype(int)):
            if np.any(delta[j]):
                result[key].append(xy[j:j+2])
    return {key:np.asarray(v) for key,v in result.items()}


def compatible_faces(i,js,ends,angle_index):
    """A tied station requires both features to have an edge along the tangent.

    Use an inclusive two-bin guard around the micro-radian key (including the
    0/π seam); the original alignment and supporting-line checks decide ties.
    """
    delta=ends[:,1]-ends[:,0];period=round(math.pi*1e6)
    tangent=np.rint(np.mod(np.arctan2(delta[:,0],-delta[:,1]),math.pi)*1e6).astype(np.int64)
    nearby=(tangent[:,None]+np.arange(-2,3))%period
    def present(ids):
        keys=np.asarray(ids,dtype=np.int64)*period+nearby
        where=np.searchsorted(angle_index,keys)
        return np.any(angle_index[np.minimum(where,len(angle_index)-1)]==keys,axis=1)
    return present(i)&present(js[:,None]) if len(angle_index) else np.zeros(len(js),bool)


def _parallel_stations(a, b, ends, tree, tol):
    """Sweep tied parallel facing edges where distinct routes can cross.

    Topology changes at projected polygon vertices. Sample each open interval
    between events instead of measuring only the arbitrary corner chosen by GEOS.
    """
    delta = ends[1] - ends[0]
    length = np.linalg.norm(delta)
    if length <= tol:
        return []
    normal = delta / length
    tangent = np.array([-normal[1], normal[0]])

    angle = math.atan2(tangent[1],tangent[0]) % math.pi
    key = round(angle*1.e6)
    def spans(faces, endpoint):
        result = []
        keys = {key-1,key,key+1}
        if key<=1 or key>=round(math.pi*1.e6)-1:
            keys.update((0,round(math.pi*1.e6)))
        for idx in keys.intersection(faces):
            edges = faces[idx]-endpoint
            same = np.all(abs(edges @ normal)<=tol,axis=1)
            result.extend(np.sort(edges[same] @ tangent,axis=1))
        return result

    samples = []
    other = spans(b, ends[1])
    if not len(other):
        return []
    for lo_a, hi_a in spans(a, ends[0]):
        for lo_b, hi_b in other:
            lo, hi = max(lo_a, lo_b), min(hi_a, hi_b)
            if hi-lo <= tol:
                continue
            corners = [ends[i] + tangent * t for i,t in ((0,lo),(1,lo),(1,hi),(0,hi))]
            hits = tree.query(Polygon(corners))
            events = [lo, hi]
            if len(hits):
                projected = (shapely.get_coordinates(tree.geometries[hits])-ends[0]) @ tangent
                events.extend(projected[(projected>lo+tol) & (projected<hi-tol)])
            stations = np.unique(events)
            samples.extend(ends + tangent * ((x+y)*.5) for x,y in zip(stations,stations[1:])
                           if y-x > tol and abs(x+y) > tol)
    return samples


def _crossings(ends, plane, tol, open_edges=None):
    """Count material intervals crossing an open nominal gate, not net IDs."""
    answers = [[] for _ in ends]
    if not len(ends) or not len(plane['routes']):
        return answers
    lines = shapely.linestrings(ends)
    ci, ri = plane['tree'].query(lines, predicate='intersects')
    if not len(ci):
        return answers
    parts, source = shapely.get_parts(shapely.intersection(plane['routes'][ri], lines[ci]), return_index=True)
    valid = (shapely.get_type_id(parts)==1) & (shapely.length(parts)>tol)
    parts, source = parts[valid], source[valid]
    if not len(parts):
        return answers
    ci, ri = ci[source], ri[source]
    delta = ends[:,1] - ends[:,0]
    gaps = np.linalg.norm(delta,axis=1)
    direction = delta/gaps[:,None]
    coords, owner = shapely.get_coordinates(parts,return_index=True)
    position = np.einsum('ij,ij->i',coords-ends[ci[owner],0],direction[ci[owner]])
    lo, hi = np.full(len(parts),np.inf), np.full(len(parts),-np.inf)
    np.minimum.at(lo,owner,position)
    np.maximum.at(hi,owner,position)
    # Metal walls cannot be mistaken for a through-route. A keepout wall may
    # be touched by a real trace, so accept its boundary endpoint explicitly.
    allow=np.zeros((len(ends),2),bool) if open_edges is None else np.asarray(open_edges,bool)
    valid = np.where(allow[ci,0],lo>=-tol,lo>tol) & np.where(allow[ci,1],hi<=gaps[ci]+tol,hi<gaps[ci]-tol)
    parts, lo, hi, ci, ri = (v[valid] for v in (parts,lo,hi,ci,ri))
    if not len(ci):
        return answers
    middle = ends[ci,0] + ((lo+hi)/2)[:,None]*direction[ci]
    cross = np.column_stack((-direction[ci,1],direction[ci,0]))
    probe = np.maximum(tol,np.minimum(plane['width']*.1,(hi-lo)*.1))[:,None]*cross
    front, back = middle+probe, middle-probe
    through = (shapely.contains_xy(plane['routes'][ri],front[:,0],front[:,1]) &
               shapely.contains_xy(plane['routes'][ri],back[:,0],back[:,1]))
    # The chord cast by an oblique crossing is too long. Twice the distance
    # from its midpoint to the nominal copper boundary gives local thickness
    # and recovers the true width of a straight trace at any crossing angle.
    ids = np.flatnonzero(through)
    boundaries = plane['boundaries'][ri[ids]]
    widths = 2*shapely.distance(shapely.points(middle[ids]),boundaries)
    # Follow the SAME connected piece of copper across the two faces of the
    # assessment rectangle. A local overlap, dead end, or two disconnected
    # stubs on opposite sides are not evidence of a through-route.
    first,last=ends[ci[ids],0],ends[ci[ids],1]
    depth=np.maximum(.65*(plane['width']+plane.get('space',plane['width'])),widths/2+2*tol)
    shift=depth[:,None]*cross[ids]
    # Clip just beyond the two test faces. Overlaying a rounded polygon
    # edge back onto the same oblique line can yield zero length in GEOS.
    clip_shift=(depth+4*tol)[:,None]*cross[ids]
    rects=shapely.polygons(np.stack([first-clip_shift,last-clip_shift,last+clip_shift,first+clip_shift],axis=1))
    front=shapely.linestrings(np.stack([first+shift,last+shift],axis=1))
    back=shapely.linestrings(np.stack([first-shift,last-shift],axis=1))
    clipped,source=shapely.get_parts(shapely.intersection(plane['routes'][ri[ids]],rects),return_index=True)
    polygon=shapely.get_type_id(clipped)==3
    clipped,source=clipped[polygon],source[polygon]
    through=((shapely.length(shapely.intersection(clipped,front[source]))>tol) &
             (shapely.length(shapely.intersection(clipped,back[source]))>tol) &
             shapely.covers(clipped,shapely.points(middle[ids[source]])))
    use=np.zeros(len(ids),bool)
    use[source[through]]=True
    hits=ids[use]
    refs_by_part={}
    # One net may contain many source polygons. Query its local index once
    # for all crossing chords, preserving source order and the length rule.
    for route in np.unique(ri[hits]):
        relevant=hits[ri[hits]==route]
        sources=plane['route_sources'][route]
        if len(sources)==1:
            for idx in relevant:refs_by_part[idx]=(sources[0][0],)
            continue
        indices=plane.setdefault('source_trees',{})
        if route not in indices:indices[route]=STRtree([g for _,g in sources])
        tree=indices[route]
        i,j=tree.query(parts[relevant])
        lengths=shapely.length(shapely.intersection(parts[relevant[i]],tree.geometries[j]))
        found=defaultdict(list)
        for idx,src in zip(relevant[i[lengths>tol]],j[lengths>tol]):found[idx].append(src)
        for idx in relevant:refs_by_part[idx]=tuple(sources[j][0] for j in sorted(found[idx]))
    for idx,width in zip(hits,widths[use]):
        answers[ci[idx]].append((float((lo[idx]+hi[idx])/2),float(width),refs_by_part[idx]))
    for group in answers:
        group.sort(key=lambda item:item[0])
    return answers


def _intruders(lines, pairs, walls, tree, tol, keepout=None):
    """Other barriers touching the interior of each gate, excluding its endpoints."""
    result = defaultdict(list)
    if not len(lines):
        return result
    ci, wi = tree.query(lines)
    keep = (wi!=pairs[ci,0]) & (wi!=pairs[ci,1])
    ci, wi = ci[keep], wi[keep]
    if not len(ci):
        return result
    ends = shapely.get_coordinates(lines).reshape(-1,2,2)
    length = np.linalg.norm(ends[:,1]-ends[:,0],axis=1)
    valid = length>2*tol
    trimmed = np.zeros_like(ends)
    direction = (ends[valid,1]-ends[valid,0])/length[valid,None]
    trimmed[valid,0] = ends[valid,0]+tol*direction
    trimmed[valid,1] = ends[valid,1]-tol*direction
    # STRtree indexes axis-aligned boxes. The box of a long diagonal segment
    # can cover most of the package. Clip against each candidate box in NumPy
    # before invoking GEOS on the actual wall. Exact touching still counts.
    bounds = shapely.bounds(walls)
    start = trimmed[ci,0]
    delta = trimmed[ci,1]-start
    parallel = delta==0
    lo = bounds[wi,:2]
    hi = bounds[wi,2:]
    stationary = np.all(~parallel | ((lo<=start) & (start<=hi)),axis=1)
    with np.errstate(divide='ignore',invalid='ignore'):
        low,high = (lo-start)/delta,(hi-start)/delta
    enter = np.maximum(0.,np.max(np.where(parallel,-np.inf,np.minimum(low,high)),axis=1))
    leave = np.minimum(1.,np.min(np.where(parallel,np.inf,np.maximum(low,high)),axis=1))
    mask = valid[ci] & stationary & (enter<=leave+8*np.finfo(float).eps)
    ci,wi = ci[mask],wi[mask]
    if len(ci):
        inner = shapely.linestrings(trimmed[ci])
        hits = shapely.intersects(walls[wi],inner)
        if keepout is not None:
            selected=np.asarray(keepout,bool)[wi]
            hits[selected] &= shapely.relate_pattern(inner[selected],walls[wi[selected]],'T********')
        for c,w in zip(ci[hits],wi[hits]):
            result[int(c)].append(int(w))
    return result


class WidthAnalyzer:
    def __init__(self, model, parameters, settings, cache_group=None):
        self.model = model
        self.settings = settings
        self.width, self.space = effective_rules(parameters,settings)
        self.annulus = parameters.annulus_um
        self.tol = max(1e-9,model.data.dbu_um*1e-5)
        self.board = model.case['board']
        self.inside = self.board.buffer(self.tol)
        shapely.prepare(self.inside)
        self.cache = SessionCache(group=cache_group)
        self.section_cache=EphemeralCache();self.demands={}
        self.catalogue_store=cache_group.values if cache_group is not None else None
        if self.catalogue_store is not None:
            self.catalogue_key=('width-catalogue-v34',model.data.geometry_id,model.data.top_name,
                                tuple(sorted(model.roles.items())),tuple(model.connections.order),
                                self.width,self.space,self.annulus,self.tol)
        self.planes = self._planes()
        from .width_probability import ChannelProbability
        self.probability=ChannelProbability(model,parameters,self.board,self.tol) if 0<parameters.target_nsigma<math.inf else None

    def close(self):
        self.section_cache.clear();self.cache.close()

    def _planes(self):
        m = self.model
        con = m.connections
        nets = UnionFind(len(m.nodes))
        for i,node in enumerate(m.nodes):
            nets.union(i,node['net'])
        landings = defaultdict(list)
        for i,node in enumerate(m.nodes):
            key, idx = node['key'], node['index']
            role = m.roles[key]
            if role not in ('运动过孔','固定过孔'):
                continue
            if role=='运动过孔':
                targets = con.fixed_interfaces(key,idx)
            else:
                targets = [(link[side],link[side+'_index'])
                           for links,side in ((con.incoming[key].get(idx,()),'upper'),(con.outgoing[key].get(idx,()),'lower'))
                           for link in links if m.roles[link[side]]=='固定RDL']
            for target in sorted(set(targets)):
                nets.union(i,m.ids[target])
            for plane in {k.split('/')[0] for k,_ in targets}:
                landings[plane].append((i,[m.nodes[m.ids[t]] for t in targets if t[0].split('/')[0]==plane]))
        moving = {nets.find(i) for i,n in enumerate(m.nodes) if m.roles[n['key']]=='运动过孔'}
        board_refs = tuple((k,i) for k,r in m.roles.items() if r=='封装边框'
                           for i in range(len(m.cache.layers[k]['geometry'])))
        edges = []
        for polygon in shapely.get_parts(self.board):
            for ring in [polygon.exterior,*polygon.interiors]:
                xy = shapely.get_coordinates(ring)
                for a,b in zip(xy,xy[1:]):
                    if np.linalg.norm(a-b)>self.tol:
                        edges.append(Wall(f'封装边 {len(edges)+1}','边框',LineString([a,b]),board_refs))
        from .width_keepout import regions
        keepouts=[Wall(f'禁布区 {refs[0][0]} #{refs[0][1]+1}','禁布区',g,refs,
                       layers=tuple(sorted({k for k,_ in refs}))) for g,refs in regions(m)]
        planes = {}
        for plane,ids in m.planes.items():
            fixed = [i for i in ids if m.roles[m.nodes[i]['key']]=='固定RDL']
            if not fixed:
                continue
            keys = tuple(sorted({m.nodes[i]['key'] for i in fixed}))
            sources, static, walls = defaultdict(list), defaultdict(list), []
            fixed_pads = {}
            for i in fixed:
                n = m.nodes[i]
                ref = (n['key'],n['index'])
                if nets.find(i) in moving:
                    sources[con.component(*ref)].append((ref,n['poly']))
                else:
                    static[nets.find(i)].append((ref,n['poly']))
            via_count = defaultdict(int)
            for _,targets in landings[plane]:
                for target in targets:
                    via_count[target['key'],target['index']]+=1
            for i,targets in landings[plane]:
                n = m.nodes[i]
                ref = (n['key'],n['index'])
                via = n['poly']
                pad = via.buffer(self.annulus,quad_segs=24) if self.annulus else via
                # A separate concentric square/rectangular or round landing is
                # identifiable in GDS. Never annex an attached track or copper pour.
                for target in targets:
                    g = target['poly']
                    vb, gb = via.bounds, g.bounds
                    margins = np.array([vb[0]-gb[0],vb[1]-gb[1],gb[2]-vb[2],gb[3]-vb[3]])
                    if (via_count[target['key'],target['index']]==1 and min(margins)>=0 and max(margins)-min(margins)<=m.data.dbu_um
                            and g.covers(via) and g.equals(g.convex_hull)
                            and (g.equals(g.envelope) or g.hausdorff_distance(via.buffer(float(min(margins)),quad_segs=24))<=m.data.dbu_um)):
                        pad = pad.union(g)
                    elif g.geom_type=='Polygon':
                        pad=embedded_pad(via,g,pad,m.data.dbu_um)
                if nets.find(i) not in moving:
                    static[nets.find(i)].append((ref,pad))
                    fixed_pads[ref]=pad
                else:
                    kind = m.roles[n['key']]+'孔盘'
                    contacts=frozenset(ref for q in targets for ref,_ in sources.get(con.component(q['key'],q['index']),()))
                    walls.append(Wall(f'{kind} {ref[0]} #{ref[1]+1}',kind,pad,(ref,),n['owner'],
                                      tuple(sorted({q['key'] for q in targets})),nets.find(i),contacts))
            for net,members in static.items():
                for g in shapely.get_parts(shapely.union_all([g for _,g in members])):
                    refs = tuple(sorted(ref for ref,h in members if h.intersects(g)))
                    name = f'固定障碍 {refs[0][0]} #{refs[0][1]+1}'
                    walls.append(Wall(name,'固定障碍',g,refs,layers=tuple(sorted({k for k,_ in refs if k in keys})),net=net,
                                      pads=tuple(fixed_pads[r] for r in refs if r in fixed_pads)))
            walls.extend(keepouts)
            route_sources = list(sources.values())
            routes = np.asarray([shapely.union_all([g for _,g in src]) for src in route_sources],object)
            shapely.prepare(routes)
            planes[plane] = dict(keys=keys,walls=walls+edges,metal_count=len(walls),routes=routes,
                                 route_sources=route_sources,tree=STRtree(routes),boundaries=shapely.boundary(routes),width=self.width,space=self.space,gates=None)
        return planes

    def _catalogue(self, plane, progress):
        walls = plane['walls']
        geometries = np.array([o.geometry for o in walls],object)
        if not len(plane['routes']):
            return []
        # The opposite sides of a concave island or a hole can carry different
        # routes. Enumerate its local faces rather than keeping only the global
        # nearest point on that island. Convex pads keep a single feature.
        pieces, parent, neighbours = [], [], []
        for wi,g in enumerate(geometries):
            if g.geom_type=='Polygon' and (g.interiors or g.convex_hull.area-g.area>max(self.tol*self.tol,self.model.data.dbu_um*g.length)):
                for ring in (g.exterior,*g.interiors):
                    xy=np.asarray(ring.coords)[:-1]
                    xy=xy[np.any(xy!=np.roll(xy,1,axis=0),axis=1)]
                    for i,p in enumerate(xy):
                        pieces.append(LineString([p,xy[(i+1)%len(xy)]]))
                        # Incident vertices on both sides of this facet are
                        # needed to distinguish a true throat from a random
                        # vertex on the same curved/concave copper boundary.
                        neighbours.append(xy[[(i-1)%len(xy),i,(i+1)%len(xy),(i+2)%len(xy)]])
                        parent.append(wi)
            else:
                pieces.append(g);parent.append(wi);neighbours.append(np.full((4,2),np.nan))
        neighbours=np.asarray(neighbours)
        pieces = np.array(pieces,object)
        parent = np.array(parent,int)
        plane['pieces'],plane['parents'] = pieces,parent
        local = np.fromiter((g is not geometries[j] for g,j in zip(pieces,parent)),bool)
        plane['local'] = local
        faces = [_faces(g) for g in pieces]
        period=round(math.pi*1e6)
        angle_index=np.asarray(sorted(i*period+k%period for i,face in enumerate(faces) for k in face),np.int64)
        metals=int(np.count_nonzero(parent<plane['metal_count']))
        profile=resource_profile()
        if metals<64:profile=dict(profile,workers=1)
        # Prepared GEOS predicates lazily build native indices. Give each
        # worker its own geometries, prepared polygons and STRtrees.
        wall_bytes=shapely.to_wkb(geometries);piece_bytes=shapely.to_wkb(pieces)
        route_bytes=shapely.to_wkb(plane['routes']);board_bytes=self.inside.wkb
        source_bytes=[[(ref,g.wkb) for ref,g in lane] for lane in plane['route_sources']]
        packed_bytes=sum(sum(map(len,arr)) for arr in (wall_bytes,piece_bytes,route_bytes))
        packed_bytes+=sum(len(wkb) for lane in source_bytes for _,wkb in lane)+len(board_bytes)
        profile=dict(profile,workers=geometry_budget(profile,packed_bytes))
        local_context=threading.local()
        def candidates_for_wall(i):
            if not hasattr(local_context,'trees'):
                geo=shapely.from_wkb(wall_bytes);shapely.prepare(geo)
                routes=shapely.from_wkb(route_bytes);shapely.prepare(routes)
                local_plane=dict(plane,routes=routes,tree=STRtree(routes),source_trees={},boundaries=shapely.boundary(routes),
                                 route_sources=[[(ref,shapely.from_wkb(wkb)) for ref,wkb in lane] for lane in source_bytes])
                inside=shapely.from_wkb(board_bytes);shapely.prepare(inside)
                local_context.trees=geo,shapely.from_wkb(piece_bytes),STRtree(geo),STRtree([*geo,*routes]),local_plane,inside
            geometries,pieces,tree,events,route_plane,inside=local_context.trees
            gates=[]
            for offset in range(i+1,len(pieces),CHUNK):
                js = np.arange(offset,min(offset+CHUNK,len(pieces)))
                lines = shapely.shortest_line(pieces[i],pieces[js])
                coordinates = shapely.get_coordinates(lines).reshape(-1,2,2)
                valid = shapely.length(lines)>self.tol
                for side,incident in ((0,neighbours[i:i+1]),(1,neighbours[js])):
                    # At a projected vertex neither adjacent edge may lead
                    # closer to the opposing wall (one-sided distance minimum).
                    endpoint=coordinates[:,side]
                    delta=coordinates[:,1-side]-endpoint
                    for vertex,adjacent in ((1,0),(2,3)):
                        touching=np.linalg.norm(endpoint-incident[:,vertex],axis=1)<=self.tol
                        tangent=incident[:,adjacent]-incident[:,vertex]
                        closer=np.einsum('ij,ij->i',delta,tangent)>self.tol*np.linalg.norm(tangent,axis=1)
                        valid &= ~(touching & closer)
                js=js[valid];xy=coordinates[valid]
                candidates=[(i,j) for j in js];ends=list(xy);anchored=[False]*len(js)
                for n in np.flatnonzero(compatible_faces(i,js,xy,angle_index)):
                    j=js[n];extra=_parallel_stations(faces[i],faces[j],xy[n],events,self.tol)
                    candidates.extend([(i,j)]*len(extra));ends.extend(extra);anchored.extend([True]*len(extra))
                if not ends:
                    continue
                ends = np.asarray(ends)
                feature_pairs = np.asarray(candidates)
                pairs = parent[feature_pairs]
                lines = shapely.linestrings(ends)
                blocked = _intruders(lines,pairs,geometries,tree,self.tol,[w.kind=='禁布区' for w in walls])
                accessible = shapely.covers(inside,lines)
                # For a concave feature, reject seams that cross its own copper.
                for side in (0,1):
                    mask = local[feature_pairs[:,side]]
                    if np.any(mask):
                        accessible[mask] &= ~shapely.relate_pattern(lines[mask],geometries[pairs[mask,side]],'T********')
                indices = [k for k,ok in enumerate(accessible) if ok and k not in blocked]
                if not indices:
                    continue
                crossing=dict(zip(indices,_crossings(ends[indices],route_plane,self.tol,
                                  np.asarray([[walls[w].kind=='禁布区' for w in pairs[k]] for k in indices],bool))))
                # For tied parallel faces, each station samples an interval
                # between nominal topology events. A missing or different
                # passage interrupts the run even on the same two straight walls.
                stations=defaultdict(list)
                for k,pair in enumerate(candidates):stations[pair].append(k)
                for feature,group in stations.items():
                    d=ends[group[0],1]-ends[group[0],0]
                    tangent=np.array([-d[1],d[0]])
                    previous=None;run=0
                    for ordinal,k in enumerate(sorted(group,key=lambda n:ends[n,0]@tangent)):
                        strands=crossing.get(k) or []
                        refs=tuple(src for _,_,src in strands)
                        if refs!=previous:run+=1;previous=refs
                        if not strands:continue
                        gap=float(np.linalg.norm(ends[k,1]-ends[k,0]))
                        gates.append(Gate(tuple(pairs[k]),feature,ends[k],anchored[k],tuple(w for _,w,_ in strands),
                                          tuple(t for t,_,_ in strands),refs,gap,(*feature,run,ordinal)))
            return gates
        # Interleave long and short strips of the triangular search. Many
        # tiny futures add contention after vectorized tangent rejection.
        batches=min(metals,max(1,4*profile['workers']))
        def solve_batch(start):
            return [(i,candidates_for_wall(i)) for i in range(start,metals,batches)]
        groups=parallel_map(solve_batch,range(batches),profile,
                            progress=lambda done,total:progress(done/max(1,total),'识别名义线路穿越'))
        ordered=sorted((i,gates) for group in groups for i,gates in group)
        return [gate for _,gates in ordered for gate in gates]

    @staticmethod
    def _templates(shapes,owners):
        owners=np.asarray(owners)
        indices=np.flatnonzero(owners>=0)
        xy,source=shapely.get_coordinates(shapes[indices],return_index=True)
        return shapes,indices,xy,owners[indices][source]

    def _placed(self,template,states,nominal):
        shapes,indices,xy,vertex_owners=template
        if nominal or not len(indices):return shapes
        placed=shapes.copy()
        coordinates=self.model.bound.transform_points(xy,vertex_owners,states)
        placed[indices]=shapely.set_coordinates(shapes[indices],coordinates)
        return placed

    def sections(self,plane,states,geometries,nominal):
        gates=plane['gates']
        if 'routes' not in plane or len(gates)<256:
            return self._sections(plane,states,geometries,nominal)
        if 'section_groups' not in plane:
            split=defaultdict(list);walls=plane['walls']
            for i,g in enumerate(gates):
                owners=tuple(sorted({walls[k].owner for k in g.pair if walls[k].owner>=0}))
                split[owners].append(i)
            groups=[]
            for owners,ids in split.items():
                wids=sorted({k for i in ids for k in gates[i].pair})
                groups.append((owners,np.array(ids),wids,section_subset(plane,ids,wids)))
            plane['section_groups']=groups
            plane['section_constants']=(np.asarray([g.ends for g in gates]),np.asarray([g.pair for g in gates]),
                                       [frozenset(v for lane in g.strand_refs for v in lane) for g in gates])
        orig,pairs,sources=plane['section_constants'];n=len(gates)
        ends=np.empty_like(orig);gaps=np.empty(n);inside=np.empty(n,bool);ref=np.empty((n,2));order=np.empty(n,bool)
        arrays=(ends,gaps,inside,ref,order)
        for owners,ids,wids,sub in plane['section_groups']:
            token=(id(plane),owners,nominal,states[list(owners)].tobytes())
            values=self.section_cache.get(token)
            if values is None:
                values=self._sections(sub,states,geometries[wids],nominal)[2:7]
                self.section_cache.put(token,values,size=sum(v.nbytes for v in values)+512)
            for dest,v in zip(arrays,values):dest[ids]=v
        return orig,pairs,ends,gaps,inside,ref,order,sources

    def _sections(self,plane,states,geometries,nominal):
        gates,walls=plane['gates'],plane['walls']
        if 'arrays' not in plane:
            orig=np.array([g.ends for g in gates]);pairs=np.array([g.pair for g in gates])
            features=np.array([g.features for g in gates]);owners=np.array([w.owner for w in walls])
            unique,inv=np.unique(features,axis=0,return_inverse=True)
            plane['arrays']=(orig,pairs,features,owners,unique,inv,
                             np.array([g.anchored for g in gates]),[frozenset(ref for strand in g.strand_refs for ref in strand) for g in gates])
        orig,pairs,features,owners,unique,inverse,is_anchor,sources=plane['arrays']
        pieces=plane['pieces'];lines=shapely.linestrings(orig)
        anchored=np.empty(0,int)
        if not nominal:
            if 'piece_templates' not in plane:
                plane['piece_templates']=self._templates(pieces,owners[plane['parents']])
            pieces=self._placed(plane['piece_templates'],states,False)
            # Only recompute truly changed feature pairs. Many parallel samples
            # share two features; their feature minimum is the same operation.
            is_moving=(owners>=0)&np.any(states[np.maximum(owners,0)]!=0,axis=1)
            changed=np.any(is_moving[pairs],axis=1)
            selected=np.flatnonzero(changed)
            if len(selected):
                inds,reverse=np.unique(inverse[selected],return_inverse=True)
                u=unique[inds]
                lines[selected]=shapely.shortest_line(pieces[u[:,0]],pieces[u[:,1]])[reverse]
            anchored=np.flatnonzero(is_anchor & changed)
        if not nominal and len(anchored):
            owners = np.array([w.owner for w in walls])
            at = pairs[anchored]
            p = self.model.bound.transform_points(orig[anchored].reshape(-1,2),owners[at].ravel(),states).reshape(-1,2,2)
            left = shapely.shortest_line(shapely.points(p[:,0]),pieces[features[anchored,1]])
            right = shapely.shortest_line(pieces[features[anchored,0]],shapely.points(p[:,1]))
            # An overlapped pair always has zero clearance, including its tied sections.
            actual_gap=shapely.length(lines[anchored])
            alternate=np.where(shapely.length(left)<=shapely.length(right),left,right)
            # If the walls tilt, a formerly parallel station is no longer the
            # throat. Keep its anchor only while it attains the actual minimum.
            use=(actual_gap>self.tol) & (shapely.length(alternate)<=actual_gap+self.tol)
            lines[anchored[use]]=alternate[use]
        gaps = shapely.length(lines)
        inside = shapely.covers(self.inside,lines)
        ends = shapely.get_coordinates(lines).reshape(-1,2,2)
        if not nominal:
            for side in (0,1):
                mask = plane['local'][features[:,side]]
                if np.any(mask):
                    inside[mask] &= ~shapely.relate_pattern(lines[mask],geometries[pairs[mask,side]],'T********')
        # A pad that has travelled through the opposite wall cannot reopen the
        # original routing order on the far side. Rotate the reference with a
        # common rigid owner, so a rigid board rotation preserves the channel.
        ref = orig[:,1]-orig[:,0]
        owners = np.array([w.owner for w in walls])
        common = (owners[pairs[:,0]]>=0) & (owners[pairs[:,0]]==owners[pairs[:,1]])
        if common.any():
            angle = states[owners[pairs[common,0]],2]
            x,y = ref[common].T
            ref[common] = np.column_stack((np.cos(angle)*x-np.sin(angle)*y,np.sin(angle)*x+np.cos(angle)*y))
        open_order = np.einsum('ij,ij->i',ends[:,1]-ends[:,0],ref)>0
        return orig,pairs,ends,gaps,inside,ref,open_order,sources

    def _measure(self, name, plane, states, progress):
        if plane['gates'] is None:
            notify=lambda n,t:progress(.65*n,t)
            if self.catalogue_store is None:
                plane['gates']=self._catalogue(plane,notify)
            else:
                def identify():
                    gates=self._catalogue(plane,notify)
                    # Share only nominal topology. Each receiver reconstructs its
                    # own GEOS objects and computes its own statistical bounds.
                    pieces=(shapely.to_wkb(plane['pieces']),plane['parents'],plane['local']) if gates else None
                    return gates,pieces
                gates,pieces=self.catalogue_store.get_or_compute((*self.catalogue_key,name),identify)
                if pieces is not None and 'pieces' not in plane:
                    wkb,plane['parents'],plane['local']=pieces
                    plane['pieces']=shapely.from_wkb(wkb)
                plane['gates']=gates
        gates, walls = plane['gates'], plane['walls']
        nominal = not np.any(states)
        if 'wall_templates' not in plane:
            shapes=np.asarray([w.geometry for w in walls],object)
            plane['wall_templates']=self._templates(shapes,[w.owner for w in walls])
        geometries=self._placed(plane['wall_templates'],states,nominal)
        retained = [dict(plane=name,name=w.name,layers=w.layers or plane['keys'],refs=w.refs,kind=w.kind,geometry=g)
                    for w,g in zip(walls[:plane['metal_count']],geometries)]
        if not gates:
            return [],retained
        orig,pairs,ends,gaps,inside,ref,open_order,sources=self.sections(plane,states,geometries,nominal)
        half_depth=.65*(self.width+self.space)
        if 'occupied' not in plane:
            original=np.asarray([w.geometry for w in walls],object)
            plane['occupied']=occupied_batches(orig,pairs,walls,original,half_depth,self.tol)
        obstacles=plane['occupied'] if nominal else occupied_batches(ends,pairs,walls,geometries,half_depth,self.tol,sources)
        # Split projections retain nominal lane ownership, but are not physical
        # cross-sections of the new pair. Draw that pair only at its own measured
        # throat with its own nominal through-routes, never on the parent axis.
        splits=defaultdict(list)
        direct=[];projections=plane.setdefault('nominal_projections',{})
        for k,g in enumerate(gates):
            spans=obstacles.get(k,[])
            if not spans:
                direct.append(k)
                continue
            if not any(walls[w].contacts.intersection(sources[k]) for w,_,_ in spans):continue
            for sub in local_openings(g,ends[k],spans,plane['occupied'].get(k,[]),walls,self.tol,evidence_only=True,projections=projections.setdefault(k,{})):
                pair=tuple(sorted(sub['pair']))
                refs=frozenset(ref for t in sub['indices'] for ref in g.strand_refs[t])
                if refs:splits[pair].append((sub['point'],refs,sub['locked']))
        rows=[]
        for k in direct:
            g=gates[k];a,b=(walls[i] for i in g.pair)
            delta=ends[k,1]-ends[k,0];gap=float(gaps[k])
            axis=delta/gap if gap>self.tol else ref[k]/np.linalg.norm(ref[k])
            now=gap if inside[k] and open_order[k] else 0.
            point=ends[k].mean(axis=0)
            refs=frozenset(ref for lane in g.strand_refs for ref in lane)
            evidence=[locked for other,lanes,locked in splits.get(tuple(sorted(g.pair)),())
                      if now>self.tol and lanes==refs and np.linalg.norm(other-point)<=2*half_depth+self.tol]
            row=self._row(name,plane,a,b,g.nominal_gap,now,point,axis,g.widths,tuple(sorted(refs)),
                          max(evidence,default=0),any(evidence),not nominal and min(a.owner,b.owner)<-1)
            row['witness_um']=ends[k].tolist()
            row['nominal_witness_um']=g.ends.tolist()
            rows.append((g,row))
        rows=self._distinct_throats(rows,half_depth)
        if self.probability is not None:
            by_name={w.name:w for w in walls}
            for row in rows:
                if row['status']!=ADEQUATE:
                    row['event_probability']=self.probability.classify(row,[by_name[name] for name in row['pair']])
        progress(1.,'通道容量检查完成')
        return rows,retained

    def margins(self,a,b):
        return tuple(0. if w.kind=='禁布区' else self.space for w in (a,b))

    def demand(self,plane,gate):
        margins=self.margins(*(plane['walls'][i] for i in gate.pair))
        return sum(max(self.width,v) for v in gate.widths)+(len(gate.widths)-1)*self.space+sum(margins)

    def _row(self,name,plane,a,b,original,gap,point,axis,widths,refs,locked,split,unresolved):
        required=len(widths);margins=self.margins(a,b);key=(original,widths,margins)
        demand=self.demands.get(key)
        if demand is None:
            keep=tuple(max(self.width,v) for v in widths)
            keep_um=sum(keep)+(required-1)*self.space+sum(margins)
            # Match the sequential additions in holds_original, including
            # its roundoff rule at exact design-rule equality.
            used=margins[0];threshold=0.
            for i,w in enumerate(keep):
                threshold=used+w+(margins[1] if i==required-1 else self.space);used+=w+self.space
            demand=(capacity(original,self.width,self.space,margins),keep_um,
                    required*self.width+(required-1)*self.space+sum(margins),threshold)
            self.demands[key]=demand
        before,keep_um,min_um,threshold=demand
        current=capacity(gap,self.width,self.space,margins)
        fits=(gap+self.tol>=self.space if not widths else current>=required)
        status=BLOCKED if not fits else ADEQUATE if threshold<=gap+8*math.ulp(max(1.,gap)) else NARROW
        return dict(plane=name,layers=plane['keys'],pair=(a.name,b.name),ends=(a.kind,b.kind),
                    boundary_refs=(a.refs,b.refs),boundary_layers=(a.layers,b.layers),refs=refs,
                    point=point.tolist(),direction=axis.tolist(),gap_um=gap,nominal_gap_um=original,
                    original=before,required=required,current=current,
                    widths_um=widths,keep_um=keep_um,
                    wall_margins_um=margins,minimum_um=min_um,status=status,slots_um=[gap] if gap else [],
                    locked=locked,split=split,unresolved=unresolved,
                    narrowing_um=max(0.,keep_um-gap) if required else 0.)

    def _distinct_throats(self,candidates,half_depth):
        """Collapse tied/overlapping samples of the same physical crossing.

        Keep disconnected local mouths, different ordered strands (including
        repeated passes of one net), and opposite faces of a concave island.
        Compare nominal sites so unrelated mouths never merge just because a
        large displacement makes their screen markers coincide.
        """
        groups=defaultdict(list)
        for gate,row in candidates:
            groups[(gate.pair,gate.strand_refs)].append((gate,row))
        result=[]
        for group in groups.values():
            if len(group)==1:
                result.append(group[0][1]);continue
            points=np.array([g.ends.mean(axis=0) for g,_ in group])
            normal=np.array([(g.ends[1]-g.ends[0])/g.nominal_gap for g,_ in group])
            clusters=UnionFind(len(group))
            ordered={gate.band:i for i,(gate,_) in enumerate(group)}
            for i,(gate,_) in enumerate(group):
                previous=ordered.get((*gate.band[:-1],gate.band[-1]-1))
                if previous is not None:clusters.union(previous,i)
            for i in range(len(group)):
                nearby=(np.linalg.norm(points[i+1:]-points[i],axis=1)<=2*half_depth+self.tol)
                aligned=normal[i+1:]@normal[i]>.995
                for offset in np.flatnonzero(nearby & aligned):
                    j=i+1+int(offset)
                    # On the same faces, only consecutive stations in an
                    # uninterrupted band may merge. A short dead zone or a
                    # third landing can split two nearby but distinct mouths.
                    if group[i][0].features!=group[j][0].features:clusters.union(i,j)
            mouths=defaultdict(list)
            for i in range(len(group)):mouths[clusters.find(i)].append(i)
            for indices in mouths.values():
                center=points[indices].mean(axis=0)
                # For tied flat faces prefer the central tested station. Keep
                # the tightest margin where motion breaks that distance tie.
                def rank(i):
                    r=group[i][1]
                    return (r['current']-r['required'],round((r['gap_um']-r['keep_um'])/self.tol),
                            np.linalg.norm(points[i]-center),i)
                row=group[min(indices,key=rank)][1]
                row['locked']=max(group[i][1]['locked'] for i in indices)
                row['split']=any(group[i][1]['split'] for i in indices)
                result.append(row)
        return result

    def frame(self, frame, progress=lambda *_:None, quick=False):
        states = frame['states']
        key = states.tobytes()
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        rows, retained = [], []
        for n,(name,plane) in enumerate(self.planes.items()):
            def update(part,text):
                progress(round(100*(n+part)/len(self.planes)),f'{name} · {text}')
            found, objects = self._measure(name,plane,states,update)
            rows.extend(found)
            retained.extend(objects)
        rows.sort(key=lambda r:({BLOCKED:0,NARROW:1,ADEQUATE:2}[r['status']],r['current']-r['required'],r['plane'],r['pair']))
        result = dict(rows=rows,retained=retained,counts={s:sum(r['status']==s for r in rows) for s in COLORS},
                      examined=len(rows),planes=list(self.planes),complete=True,scope=SCOPE,
                      parameters=dict(width_um=self.width,space_um=self.space,enclosure_um=self.annulus))
        if not np.any(states):
            self.cache.pin(key)
        # Intermediate frames are already retained by the animation packet.
        if not quick:self.cache[key] = result
        progress(100,'宽度分析完成')
        return result
