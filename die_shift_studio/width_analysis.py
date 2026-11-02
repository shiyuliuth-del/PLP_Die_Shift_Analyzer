"""Capacity of nominal route crossings between landing pads, fixed islands and the package.

Lengths are in micrometres. A gate retains its nominal demand and source polygon
references at every pose. The nominal catalogue is built once per engine.
"""
from collections import defaultdict
from dataclasses import dataclass
import math
import numpy as np
import shapely
from shapely.geometry import LineString, Point, Polygon
from shapely.strtree import STRtree
from .drc_rules import effective_rules
from .session_cache import SessionCache
from .stack import UnionFind
from .neighbor_channels import occupied, local_openings
from .landing_geometry import embedded_pad

ADEQUATE, NARROW, BLOCKED = '宽度足够','需局部收窄','宽度不足'
COLORS = {BLOCKED:'#c44956', NARROW:'#bd8127', ADEQUATE:'#498d79'}
SCOPE = '固定 RDL 名义通道的局部容量。每个空隙的两侧各保留一道线距。'
CHUNK = 256


def capacity(gap, width, space):
    """Largest integer n >= 0 satisfying n*w + (n+1)*s <= gap."""
    if min(width, space) <= 0:
        raise ValueError('通道的有效线宽和线距必须大于零')
    # Allow floating-point roundoff at an exact design-rule equality.
    ratio = (gap - space) / (width + space)
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
    center = np.asarray(row['point'])
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


def _crossings(ends, plane, tol):
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
    valid = (lo>tol) & (hi<gaps[ci]-tol)
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
    # Copper running along the gate can cover both epsilon probes without
    # conveying any line across it. For long chords, test a transverse strip
    # beyond the two sides of the original linewidth. A straight parallel
    # track stops inside the strip, an oblique through-route reaches both sides.
    long = (hi[ids]-lo[ids])>widths+2*tol
    use = np.ones(len(ids),bool)
    if np.any(long):
        probe_ids = ids[long]
        c = ci[probe_ids]
        first = ends[c,0]+lo[probe_ids,None]*direction[c]
        last = ends[c,0]+hi[probe_ids,None]*direction[c]
        shift = (widths[long]/2+2*tol)[:,None]*cross[probe_ids]
        traces = plane['routes'][ri[probe_ids]]
        front = shapely.linestrings(np.stack((first+shift,last+shift),axis=1))
        back = shapely.linestrings(np.stack((first-shift,last-shift),axis=1))
        use[long] = shapely.intersects(traces,front) & shapely.intersects(traces,back)
    for idx,width in zip(ids[use],widths[use]):
        sources = plane['route_sources'][int(ri[idx])]
        refs = ((sources[0][0],) if len(sources)==1 else
                tuple(ref for ref,geometry in sources if geometry.intersection(parts[idx]).length>tol))
        answers[ci[idx]].append((float((lo[idx]+hi[idx])/2),float(width),refs))
    for group in answers:
        group.sort(key=lambda item:item[0])
    return answers


def _intruders(lines, pairs, walls, tree, tol):
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
        self.planes = self._planes()

    def close(self):
        self.cache.close()

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
            route_sources = list(sources.values())
            routes = np.asarray([shapely.union_all([g for _,g in src]) for src in route_sources],object)
            shapely.prepare(routes)
            planes[plane] = dict(keys=keys,walls=walls+edges,metal_count=len(walls),routes=routes,
                                 route_sources=route_sources,tree=STRtree(routes),boundaries=shapely.boundary(routes),width=self.width,gates=None)
        return planes

    def _catalogue(self, plane, progress):
        walls = plane['walls']
        geometries = np.array([o.geometry for o in walls],object)
        shapely.prepare(geometries)
        tree = STRtree(geometries)
        if not len(plane['routes']):
            return []
        # The opposite sides of a concave island or a hole can carry different
        # routes. Enumerate its local faces rather than keeping only the global
        # nearest point on that island. Convex pads keep a single feature.
        pieces, parent = [], []
        for wi,g in enumerate(geometries):
            parts = [g]
            if g.geom_type=='Polygon' and (g.interiors or g.convex_hull.area-g.area>max(self.tol*self.tol,self.model.data.dbu_um*g.length)):
                parts = [LineString([a,b]) for ring in (g.exterior,*g.interiors)
                         for a,b in zip(ring.coords, list(ring.coords)[1:]) if a!=b]
            pieces.extend(parts)
            parent.extend([wi]*len(parts))
        pieces = np.array(pieces,object)
        parent = np.array(parent,int)
        plane['pieces'],plane['parents'] = pieces,parent
        local = np.fromiter((g is not geometries[j] for g,j in zip(pieces,parent)),bool)
        plane['local'] = local
        faces = [_faces(g) for g in pieces]
        events = STRtree([*geometries,*plane['routes']])
        gates = []
        metals = int(np.count_nonzero(parent<plane['metal_count']))
        for i in range(metals):
            for offset in range(i+1,len(pieces),CHUNK):
                js = np.arange(offset,min(offset+CHUNK,len(pieces)))
                lines = shapely.shortest_line(pieces[i],pieces[js])
                candidates, ends, anchored = [], [], []
                coordinates = shapely.get_coordinates(lines).reshape(-1,2,2)
                valid = shapely.length(lines)>self.tol
                for j,xy in zip(js[valid],coordinates[valid]):
                    sections = [xy,*_parallel_stations(faces[i],faces[j],xy,events,self.tol)]
                    for k,section in enumerate(sections):
                        candidates.append((i,j)); ends.append(section); anchored.append(k>0)
                if not ends:
                    continue
                ends = np.asarray(ends)
                feature_pairs = np.asarray(candidates)
                pairs = parent[feature_pairs]
                lines = shapely.linestrings(ends)
                blocked = _intruders(lines,pairs,geometries,tree,self.tol)
                accessible = shapely.covers(self.inside,lines)
                # For a concave feature, reject seams that cross its own copper.
                for side in (0,1):
                    mask = local[feature_pairs[:,side]]
                    if np.any(mask):
                        accessible[mask] &= ~shapely.relate_pattern(lines[mask],geometries[pairs[mask,side]],'T********')
                indices = [k for k,ok in enumerate(accessible) if ok and k not in blocked]
                if not indices:
                    continue
                counts = _crossings(ends[indices],plane,self.tol)
                for k,strands in zip(indices,counts):
                    if not strands:
                        continue
                    gap = float(np.linalg.norm(ends[k,1]-ends[k,0]))
                    gates.append(Gate(tuple(pairs[k]),tuple(feature_pairs[k]),ends[k],anchored[k],tuple(w for _,w,_ in strands),
                                      tuple(t for t,_,_ in strands),tuple(src for _,_,src in strands),gap))
            progress((i+1)/max(1,metals),'识别名义线路穿越')
        return gates

    def _measure(self, name, plane, states, progress):
        if plane['gates'] is None:
            plane['gates'] = self._catalogue(plane,lambda n,t:progress(.65*n,t))
        gates, walls = plane['gates'], plane['walls']
        nominal = not np.any(states)
        geometries = np.array([w.geometry if nominal or w.owner<0 else self.model.moved(w.geometry,w.owner,states)
                               for w in walls],object)
        retained = [dict(plane=name,name=w.name,layers=w.layers or plane['keys'],refs=w.refs,kind=w.kind,geometry=g)
                    for w,g in zip(walls[:plane['metal_count']],geometries)]
        rigid=self._rigid_clearances(name,plane,geometries)
        if not gates:
            return rigid,retained
        pairs = np.array([g.pair for g in gates])
        orig = np.array([g.ends for g in gates])
        features = np.array([g.features for g in gates])
        pieces = plane['pieces']
        if not nominal:
            pieces = np.array([geometries[parent] if p is walls[parent].geometry else
                               self.model.moved(p,walls[parent].owner,states) for p,parent in zip(pieces,plane['parents'])],object)
        lines = shapely.linestrings(orig) if nominal else shapely.shortest_line(pieces[features[:,0]],pieces[features[:,1]])
        anchored = np.where([g.anchored for g in gates])[0]
        if not nominal and len(anchored):
            owners = np.array([w.owner for w in walls])
            at = pairs[anchored]
            p = self.model.bound.transform_points(orig[anchored].reshape(-1,2),owners[at].ravel(),states).reshape(-1,2,2)
            left = shapely.shortest_line(shapely.points(p[:,0]),pieces[features[anchored,1]])
            right = shapely.shortest_line(pieces[features[anchored,0]],shapely.points(p[:,1]))
            # An overlapped pair always has zero clearance, including its tied sections.
            use = shapely.length(lines[anchored]) > self.tol
            lines[anchored[use]] = np.where(shapely.length(left[use])<=shapely.length(right[use]),left[use],right[use])
        gaps = shapely.length(lines)
        inside = shapely.covers(self.inside,lines)
        ends = shapely.get_coordinates(lines).reshape(-1,2,2)
        if not nominal:
            for side in (0,1):
                mask = plane['local'][features[:,side]]
                if np.any(mask):
                    inside[mask] &= ~shapely.relate_pattern(lines[mask],geometries[pairs[mask,side]],'T********')
        half_depth=.65*(self.width+self.space)
        if 'occupied' not in plane:
            original=np.asarray([w.geometry for w in walls],object)
            plane['occupied']=occupied(orig,pairs,walls,original,half_depth,self.tol)
        obstacles=plane['occupied'] if nominal else occupied(ends,pairs,walls,geometries,half_depth,self.tol)
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
        rows = []
        for k,g in enumerate(gates):
            gap=float(gaps[k])
            axis=(ends[k,1]-ends[k,0])/gap if gap>self.tol else ref[k]/np.linalg.norm(ref[k])
            spans=obstacles.get(k,[])
            openings=(local_openings(g,ends[k],spans,plane['occupied'].get(k,[]),walls,self.tol) if spans else
                      [dict(pair=g.pair,indices=range(len(g.widths)),nominal_gap=g.nominal_gap,
                            gap=gap,point=ends[k].mean(axis=0),locked=0,split=False)])
            for opening in openings:
                i,j=opening['pair'];a,b=walls[i],walls[j]
                now=opening['gap'] if inside[k] and open_order[k] else 0.
                chosen=opening['indices']
                widths=tuple(g.widths[t] for t in chosen)
                refs=tuple(sorted({ref for t in chosen for ref in g.strand_refs[t]}))
                rows.append(self._row(name,plane,a,b,opening['nominal_gap'],now,opening['point'],axis,widths,refs,
                                      opening['locked'],opening['split'],not nominal and min(a.owner,b.owner)<-1))
        represented={tuple(sorted(r['pair'])) for r in rows if r['status']==BLOCKED}
        rows.extend(r for r in rigid if tuple(sorted(r['pair'])) not in represented)
        progress(1.,'通道容量检查完成')
        return rows,retained

    def _row(self,name,plane,a,b,original,gap,point,axis,widths,refs,locked,split,unresolved):
        required=len(widths)
        current=capacity(gap,self.width,self.space)
        keep=tuple(max(self.width,v) for v in widths)
        fits=(gap+self.tol>=self.space if not widths else current>=required)
        status=BLOCKED if not fits else ADEQUATE if holds_original([gap],keep,self.space) else NARROW
        return dict(plane=name,layers=plane['keys'],pair=(a.name,b.name),ends=(a.kind,b.kind),
                    boundary_refs=(a.refs,b.refs),boundary_layers=(a.layers,b.layers),refs=refs,
                    point=point.tolist(),direction=axis.tolist(),gap_um=gap,nominal_gap_um=original,
                    original=capacity(original,self.width,self.space),required=required,current=current,
                    widths_um=widths,keep_um=sum(keep)+(required+1)*self.space,
                    minimum_um=required*self.width+(required+1)*self.space,status=status,slots_um=[gap] if gap else [],
                    locked=locked,split=split,unresolved=unresolved,
                    narrowing_um=max(0.,sum(keep)+(required+1)*self.space-gap) if required else 0.)

    def _rigid_clearances(self,name,plane,geometries):
        """A neighbour can violate the wall spacing even with no flexible wire."""
        walls=plane['walls'];rows=[]
        pairs=STRtree(geometries).query(geometries[:plane['metal_count']],predicate='dwithin',distance=self.space).T
        pairs=np.array([(i,j) for i,j in pairs if i<j and (walls[i].net!=walls[j].net or walls[j].kind=='边框')],int)
        if not len(pairs):return rows
        lines=shapely.shortest_line(geometries[pairs[:,0]],geometries[pairs[:,1]])
        ends=shapely.get_coordinates(lines).reshape(-1,2,2)
        spans=occupied(ends,pairs,walls,geometries,.65*(self.width+self.space),self.tol)
        for index,(i,j) in enumerate(pairs):
            delta=ends[index,1]-ends[index,0];gap=float(np.linalg.norm(delta))
            if gap+self.tol>=self.space or spans.get(index):continue
            if gap>self.tol:axis=delta/gap
            else:
                old=shapely.get_coordinates(shapely.shortest_line(walls[i].geometry,walls[j].geometry))
                v=old[-1]-old[0];norm=np.linalg.norm(v);axis=v/norm if norm>self.tol else np.array([1.,0.])
            rows.append(self._row(name,plane,walls[i],walls[j],walls[i].geometry.distance(walls[j].geometry),
                                  gap,ends[index].mean(axis=0),axis,(),(),0,False,False))
        return rows

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
        self.cache[key] = result
        progress(100,'宽度分析完成')
        return result
