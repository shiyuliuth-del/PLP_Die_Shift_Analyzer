"""Preview the production RDL reconstruction: split, shift, then cover the seam.

Production does not keep the fixed RDL rigid. An operator marks the parts that
stay, the parts that follow a die are shifted onto their vias, and the fissure
left at the seam is covered by a patch of copper.

Software has to guess that split, so this module guesses out loud. For every
moving via whose copper is fixed RDL, the cut is a straight line perpendicular
to the copper that runs through the via — so the cut meets both sides of the
trace at a right angle, exactly how an operator cuts a route.

Landings of the same die that sit close together on one fixed-RDL feature are
one chain: they must travel together, so the cut never falls between them. The
cut is placed a stated distance beyond the FARTHEST landing of the chain, on the
side where the rest of the network lies, so the cut distance is always measured
from that farthest landing and the moved block stays around the chain instead of
dragging the rest of the feature along. A chain whose landings are far apart is
split into separate chains.

The cover is the polygon formed by the four corners of the two cut faces (the
movable one and the stationary one), so it spans the whole fissure instead of
just a line across it; it is added whenever a fissure is left open, whatever the
gap is, and skipped only when the two faces already lie on top of each other.

On a real drawing one fixed-RDL polygon often spans several chips. Copper may
follow only the die whose chip frame (128/0 芯片边框) it lies in: the movable
block is clipped to that frame, the copper beyond the frame stays put (it belongs
to the neighbouring die), and the frame edge becomes a seam of its own — covered
the same way, from the cut faces' four corners. Only the landings are allowed to
cross the frame edge, because a via must keep its own landing.

The guess is reported, never hidden: every piece lists how much copper it moved,
where the cut went, the seam gap and the area of the cover, and the frame says
the split is automatic and may differ from the operator's. Only the seam is
invented here; the die motion itself is the same rigid transform the vias use, so
a movable part always stays aligned with the via it belongs to.
"""
import numpy as np
import shapely
from .session_cache import SessionCache
from .preview import rigid_transform
from .drc_rules import effective_rules

# Distance from the farthest landing of a chain to the cut when no explicit
# distance is set.
AUTO_LANDING_DIAMETERS=2.5

SCOPE=('重构预演：软件自动把“运动过孔下方的固定RDL铜”划为可移动区——切分线垂直于该过孔下方那段铜的走向'
       '（与走线两侧都成90度）；同一块固定RDL上属于同一DIE、彼此相距不远的多个落盘视为一条串，'
       '切口不会落在两个相邻落盘之间，而是从这条串里最远的那一端再向外'
       '一段距离（默认自动搜索：从落盘边缘沿走线向外，止于同网络汇合节点或下一个同网络落盘，'
       '取横向最窄、附近最直处；也可在设置里写死“切口距离 D”），网络那一侧保持不动，'
       '含落盘的那一段为移动区；'
       '移动区按所属DIE的刚体变换移动到过孔新位置，'
       '两个切口面的4个端点围成的多边形作为补线盖住裂隙——不论裂隙大小（是否短路）都会补，'
       '只有两个切口面完全重合、或缝隙已被铜盖住时才不重复补。'
       '切口位置以“重构后的整条链路不与其他网络短路”为约束搜索：每个候选位置都量一次移动后的铜和补线到同层其它网络铜的距离，'
       '优先取满足有效线距的位置；当前姿态下无论切在哪里都避不开时，取间距最大的位置并标注“间距不足”。'
       '同一块固定RDL若跨多个芯片，移动区只取本DIE芯片边框（128/0）以内的铜，边框外的铜留在原位归相邻DIE，'
       '边框线本身也是一条切口并按同样的4端点补线；只有落盘允许跨出边框，以免过孔失去落盘。'
       '自动划分可能与人工划分不同，移动区面积与补线面积都列在表中供核对；'
       '这是重构预演，不代表产线实际做法、整图可布通或量产良率。')


def transform(geometry,center,state):
    """Rigid transform of any geometry, matching how the vias are moved."""
    return shapely.set_coordinates(geometry,rigid_transform(shapely.get_coordinates(geometry),center,state))


def half_plane(origin,normal,size):
    """Square covering the half-plane behind ``origin`` along ``normal``."""
    tangent=np.array([-normal[1],normal[0]])
    corners=[origin+tangent*size,origin-tangent*size,origin-tangent*size-normal*size,origin+tangent*size-normal*size]
    return shapely.Polygon(corners)


def bridge_quads(faces,center,state,expand_um=0.):
    """Cover polygons: each cut face and its moved copy give four corners.

    The cover is not a thin trace but the quad bounded by the stationary cut face
    and the movable one, so it spans the whole seam. ``expand_um`` optionally
    widens every side of that quad.
    """
    quads=[]
    for face in faces:
        coordinates=shapely.get_coordinates(face)
        if len(coordinates)<2:continue
        moved_coordinates=rigid_transform(coordinates,center,state)
        quad=shapely.Polygon(np.vstack([coordinates,moved_coordinates[::-1]]))
        if not quad.is_valid:quad=shapely.make_valid(quad)
        if quad.is_empty:continue
        if expand_um>0.:quad=shapely.buffer(quad,expand_um,join_style=2,quad_segs=8)
        if quad.is_empty:continue
        quads.append(quad)
    return quads


def line_parts(geometry):
    """LineString pieces of any geometry (a MultiLineString or a collection)."""
    if geometry is None or geometry.is_empty:return []
    if geometry.geom_type=='LineString':return [geometry]
    return [q for q in getattr(geometry,'geoms',[]) if q.geom_type=='LineString' and not q.is_empty]


class Reconstructor:
    # Chain blocks are reusable across frames: only the dragged chain's key
    # changes when a cut is pinned, so a drag recomputes one chain, not all.
    _blocks_cache = {}
    def __init__(self,model,parameters,settings,cache_group=None):
        self.model=model;self.p=parameters;self.settings=settings;self.cache=SessionCache(group=cache_group)
        self.tol=max(1e-9,model.data.dbu_um)
        # Line spacing the reconstruction must keep to other nets (the same
        # effective rule the DRC and the air-gap analysis use).
        self._rule_width,self.rule_space=effective_rules(parameters,settings)
        self.length_um=max(0.,float(getattr(settings,'reconstruction_length_um',0.)))
        # Outward expansion of the cover polygon: 0 = exactly the four cut-face
        # corners, which is the default reconstruction.
        self.width_um=max(0.,float(getattr(settings,'reconstruction_width_um',0.)))
        self.overlap_um=max(0.,float(getattr(settings,'reconstruction_overlap_um',getattr(settings,'width_overlap_um',0.))))
        # Manual cut planes drawn by the user: (layer, feature, vias) -> plane.
        # A plane pins that chain's cut; the rest of the rule (perpendicular to
        # the route, movable side, cover, spacing check) still applies.
        self.cuts={}
        self._last_planes={}
        for row in getattr(settings,'reconstruction_cuts',()) or ():
            layer,index,vias,nx,ny,ox,oy=row
            self.cuts[(str(layer),int(index),tuple(sorted(int(v) for v in vias)))]=(
                float(nx),float(ny),float(ox),float(oy))
        self.vias=[]
        for key,role in model.roles.items():
            if role!='运动过孔':continue
            for index in range(len(model.cache.layers[key]['geometry'])):
                targets=model.connections.fixed_interfaces(key,index)
                if not targets:continue
                node=model.nodes[model.ids[key,index]]
                center=np.asarray(node['poly'].centroid.coords[0],float)
                land=shapely.buffer(node['poly'],self.overlap_um,quad_segs=32) if self.overlap_um>self.tol else node['poly']
                self.vias.append(dict(key=key,index=index,owner=int(node['owner']),poly=node['poly'],center=center,
                                      land=land,targets=[(k,i) for k,i in targets],
                                      land_diameter=2.*float(np.sqrt(max(land.area,0.)/np.pi))))

    def close(self):self.cache.close()

    def _copper(self,layer,index,replacements):
        # Accepted deformation already moved some fixed copper; start from it.
        for item in replacements.get(layer,[]):
            if index in item['indices']:return item['geometry']
        return self.model.cache.layers[layer]['geometry'][index]

    def _axis(self,polygon,land):
        """Local direction of the copper where the via sits.

        The cut is taken across this direction, so a 45° route is cut at 135°:
        the cut meets both sides of the trace at a right angle. Only the copper
        near the via votes, so a long meander does not tilt the local cut.
        """
        window=shapely.buffer(land,3.*self._diameter(land),quad_segs=16)
        patch=shapely.intersection(polygon,window)
        if patch.is_empty:patch=polygon
        rect=shapely.minimum_rotated_rectangle(patch)
        coordinates=shapely.get_coordinates(rect)
        if len(coordinates)<4:return np.array([1.,0.])
        edges=np.diff(np.vstack([coordinates[:4],coordinates[:1]]),axis=0)
        lengths=np.linalg.norm(edges,axis=1)
        edge=edges[int(np.argmax(lengths))]
        span=float(np.linalg.norm(edge))
        return edge/span if span>self.tol else np.array([1.,0.])

    def _aspect(self,polygon,land):
        """Length/width ratio of the copper right around one landing.

        A trace is long and thin (the cut has a clear direction to be square to);
        a landing pad is compact, and then the motion direction decides the cut
        instead, because a cut across a pad would slide along the motion and leave
        the two pieces touching however narrow it is.
        """
        window=shapely.buffer(land,1.5*self._diameter(land),quad_segs=16)
        patch=shapely.intersection(polygon,window)
        if patch.is_empty or patch.area<=self.tol**2:return 1.
        rect=shapely.minimum_rotated_rectangle(patch)
        coordinates=shapely.get_coordinates(rect)
        if len(coordinates)<4:return 1.
        edges=np.diff(np.vstack([coordinates[:4],coordinates[:1]]),axis=0)
        lengths=np.sort(np.linalg.norm(edges,axis=1))
        short=float(lengths[0]);long=float(lengths[-1])
        return long/short if short>self.tol else 1.

    def _local_direction(self,polygon,point,radius):
        """Direction of the copper right around a point (None when there is none)."""
        window=shapely.buffer(shapely.Point(point),max(radius,self.tol*8.),quad_segs=8)
        patch=shapely.intersection(polygon,window)
        if patch.is_empty or patch.area<=self.tol**2:return None
        rect=shapely.minimum_rotated_rectangle(patch)
        coordinates=shapely.get_coordinates(rect)
        if len(coordinates)<4:return None
        edges=np.diff(np.vstack([coordinates[:4],coordinates[:1]]),axis=0)
        lengths=np.linalg.norm(edges,axis=1)
        edge=edges[int(np.argmax(lengths))];span=float(np.linalg.norm(edge))
        return edge/span if span>self.tol else None

    @staticmethod
    def _diameter(geometry):
        return 2.*float(np.sqrt(max(geometry.area,0.)/np.pi))

    def _conflict_limit(self):
        """How far away a candidate is still "close" for the safety test."""
        return max(2.*self.rule_space,10.)

    def _foreign_gap(self,geometry,tree,nets,limit):
        """Nearest copper of another net, or (None,None) when there is none.

        ``nets`` is a list of (net, geometry); the returned index refers to it,
        ``limit`` caps the search so a far-away net does not cost a distance
        computation per candidate.
        """
        if geometry is None or geometry.is_empty or tree is None or not nets:
            return None,None
        bounds=geometry.bounds
        near=shapely.box(bounds[0]-limit,bounds[1]-limit,bounds[2]+limit,bounds[3]+limit)
        hits=tree.query(near)
        if not len(hits):return None,float(limit)
        best=float('inf');which=None
        for i in np.asarray(hits,dtype=int).ravel():
            d=float(shapely.distance(geometry,nets[int(i)][1]))
            if d<best:best=d;which=int(i)
        return which,best

    @staticmethod
    def _box_gap(a,b):
        return float(np.hypot(max(b[0]-a[2],a[0]-b[2],0.),max(b[1]-a[3],a[1]-b[3],0.)))

    def _candidate_conflict(self,movable,faces,center,state,foreign):
        """Whether the shifted piece (plus its cover) reaches another net."""
        if foreign is None:return False,None,None
        tree,nets=foreign
        limit=self._conflict_limit()
        # Cheap test first: the shifted BOUNDING BOX against the neighbours'
        # boxes.  Most candidates sit far from every other net, and transforming
        # a large polygon just to prove that is what makes a full drawing slow.
        corners=shapely.get_coordinates(shapely.box(*movable.bounds))
        corners=rigid_transform(corners,center,state)
        hits=tree.query(shapely.box(corners[:,0].min()-limit,corners[:,1].min()-limit,
                                    corners[:,0].max()+limit,corners[:,1].max()+limit),
                        predicate='dwithin',distance=limit)
        if not len(hits):return False,float(limit),None
        moved=transform(movable,center,state)
        quads=bridge_quads(faces,center,state,self.width_um) if faces else []
        threshold=self.rule_space-self.tol
        probes=[(probe,probe.bounds) for probe in [moved]+quads]
        best=float('inf');which=None
        # On a dense layer the tree can return dozens of neighbours for one
        # candidate; only the nearest few can be the minimum, so rank them by
        # box distance and measure those.
        box=moved.bounds
        ranked=sorted((int(i) for i in np.asarray(hits,dtype=int).ravel()),
                      key=lambda i:self._box_gap(box,nets[i][1].bounds))[:8]
        for i in ranked:
            geometry=nets[int(i)][1]
            x0,y0,x1,y1=geometry.bounds
            for probe,(a0,b0,a1,b1) in probes:
                # Numeric box distance first: on a dense layer the tree still
                # returns dozens of neighbours, and a GEOS predicate call each
                # is what made the search slow.
                dx=max(x0-a1,a0-x1,0.);dy=max(y0-b1,b0-y1,0.)
                if dx*dx+dy*dy>threshold*threshold:continue
                if not shapely.dwithin(probe,geometry,threshold):continue
                d=float(shapely.distance(probe,geometry))
                if d<best:best=d;which=int(i)
        if best==float('inf'):
            nearest=min(float(shapely.distance(moved,nets[int(i)][1]))
                        for i in np.asarray(hits,dtype=int).ravel())
            return False,max(nearest,threshold),None
        return True,best,which

    def _frames(self):
        """Chip frames (芯片边框) by layer, for clipping the movable blocks."""
        if getattr(self,'_frame_cache',None) is None:
            frames=[]
            for key,role in self.model.roles.items():
                if role!='芯片边框':continue
                for geometry in self.model.cache.layers.get(key,{}).get('geometry',[]):
                    if geometry is not None and not geometry.is_empty:
                        frames.append((key,geometry))
            self._frame_cache=frames
        return self._frame_cache

    def _own_frame(self,owner):
        """The chip frame that hosts this die, or None when no frame covers it."""
        point=shapely.Point(np.asarray(self.model.bound.centers[owner],float))
        for key,geometry in self._frames():
            bounds=geometry.bounds
            if bounds[0]<=point.x<=bounds[2] and bounds[1]<=point.y<=bounds[3]:
                return geometry
        return None

    def _clip(self,movable,frame,landings):
        """Keep the movable copper inside the die's chip frame, landings whole.

        One fixed-RDL polygon can span several chips. Copper may follow only the
        die whose chip frame it lies in, so the block is clipped to the frame and
        the frame edge becomes a cut face of its own; the landings are excepted,
        since a via must keep the copper it lands on.
        """
        if frame is None:return movable
        inside=shapely.intersection(movable,frame)
        keep=[inside] if not inside.is_empty else []
        for land in landings:
            part=shapely.difference(shapely.intersection(land,movable),frame)
            if part.is_empty:continue
            keep.append(part)
        if not keep:return shapely.Polygon()
        return shapely.union_all(keep) if len(keep)>1 else keep[0]

    def _region_faces(self,region,polygon,plane_faces,frame):
        """Cut faces of the final movable region.

        The plane faces survive only where the region still reaches them (the clip
        to the chip frame may have removed that part); the chip frame edge adds
        faces of its own where copper really continues on the far side, since a
        frame edge that runs along the copper's outline has nothing to cover.
        """
        faces=[q for q in plane_faces
               if not q.is_empty and shapely.distance(q,region)<=self.tol*4]
        if frame is not None and not region.is_empty:
            outside=shapely.difference(polygon,region)
            for edge in line_parts(shapely.intersection(region.boundary,frame.boundary)):
                if edge.length<=self.tol:continue
                if outside.is_empty or shapely.distance(edge,outside)>self.tol*4:continue
                faces.append(edge)
        return faces

    def _merge(self,regions):
        """Union blocks of one die that overlap (their seams would double up)."""
        if len(regions)<2:return regions
        parent=list(range(len(regions)))
        def root(i):
            while parent[i]!=i:parent[i]=parent[parent[i]];i=parent[i]
            return i
        for a in range(len(regions)):
            for b in range(a+1,len(regions)):
                if shapely.intersection(regions[a]['movable'],regions[b]['movable']).area>self.tol**2:
                    ra,rb=root(a),root(b)
                    if ra!=rb:parent[rb]=ra
        grouped={}
        for i,region in enumerate(regions):grouped.setdefault(root(i),[]).append(region)
        merged=[]
        for _,items in sorted(grouped.items()):
            merged.append(dict(movable=shapely.union_all([q['movable'] for q in items]),
                               faces=[f for q in items for f in q.get('faces',[])],
                               distance=max(q['distance'] for q in items),
                               span=max(q['span'] for q in items),
                               owner=items[0]['owner'],frame=items[0].get('frame'),
                               conflict=any(bool(q.get('conflict')) for q in items),
                               manual=any(bool(q.get('manual')) for q in items),
                               clamped=any(bool(q.get('clamped')) for q in items),
                               window=next((q['window'] for q in items if q.get('window')),None),
                               gap=min((q['gap'] for q in items if q.get('gap') is not None),
                                       default=None),
                               cluster=[v for q in items for v in q['cluster']]))
        return merged

    def _step(self,diameter):
        """Stated distance from the farthest landing of a chain to the cut."""
        return self.length_um if self.length_um>0. else AUTO_LANDING_DIAMETERS*diameter

    def _chain_axis(self,polygon,cluster):
        """Direction through a chain of landings, else the local copper."""
        if len(cluster)>1:
            centres=np.array([v['center'] for v in cluster],float)
            gaps=np.linalg.norm(centres[:,None,:]-centres[None,:,:],axis=2)
            first,second=np.unravel_index(int(np.argmax(gaps)),gaps.shape)
            delta=centres[second]-centres[first];span=float(np.linalg.norm(delta))
            if span>self.tol:return delta/span
        return self._axis(polygon,cluster[0]['land'])

    def _clusters(self,threshold,vias):
        """Landings of one die on one feature, grouped by how close they are.

        Landings whose copper is within ``threshold`` of each other travel
        together, so no cut may fall between them; distant ones are separate
        blocks.
        """
        count=len(vias)
        if count<2:return [list(vias)]
        parent=list(range(count))
        def root(i):
            while parent[i]!=i:parent[i]=parent[parent[i]];i=parent[i]
            return i
        for a in range(count):
            for b in range(a+1,count):
                if shapely.distance(vias[a]['land'],vias[b]['land'])<=threshold:
                    ra,rb=root(a),root(b)
                    if ra!=rb:parent[rb]=ra
        grouped={}
        for i in range(count):grouped.setdefault(root(i),[]).append(vias[i])
        return [sorted(q,key=lambda v:v['index']) for _,q in sorted(grouped.items())]

    def _faces_at(self,polygon,origin,normal,tangent,size):
        """Cut face: where the plane really crosses copper.

        It cannot be taken from the half-plane polygon: that polygon's own
        boundary IS this plane, and intersecting a line with the edge it lies on
        keeps only float slivers (a 25 μm trace came back as a 3 μm stub). So the
        line is crossed with the untouched copper and only the parts that have
        copper on both sides of the plane are kept.
        """
        probe=max(self.tol*4.,.01)
        line=shapely.LineString([origin-tangent*size,origin+tangent*size])
        faces=[]
        for piece in line_parts(shapely.intersection(line,polygon)):
            if piece.length<=self.tol:continue
            mid=np.asarray(piece.interpolate(.5,normalized=True).coords[0],float)
            if (shapely.contains(polygon,shapely.Point(mid-normal*probe))
                    and shapely.contains(polygon,shapely.Point(mid+normal*probe))):
                faces.append(piece)
        return faces

    def _plane_candidate(self,polygon,pivot,coordinates,others,size,tangent,plane):
        """Candidate cut built from a plane the user dragged.

        The plane is clamped to the same stretch the search may use (outside the
        landings, before the next landing of the same net), so a stale or far
        drag cannot put the cut inside a landing or past a junction. The direction
        is kept exactly as given: only its position moves.
        """
        nx,ny,ox,oy=plane
        normal=np.array([float(nx),float(ny)],float)
        norm=float(np.linalg.norm(normal))
        if norm<=0.:return None
        normal=normal/norm
        origin=np.array([float(ox),float(oy)],float)
        extent=float(np.max((coordinates-pivot)@normal))
        copper=shapely.get_coordinates(polygon)
        far=float(np.max((copper-pivot)@normal))
        # A hand-placed cut may use the whole stretch of its own copper. The
        # automatic search stops before the next same-net landing, but the user
        # asked for the cut explicitly, so only the landing itself and the end
        # of the copper are hard limits here.
        low=extent+self.tol*4.
        high=far-self.tol*4.
        along=float(np.dot(origin-pivot,normal))
        wanted=along
        if high>low:
            along=min(max(along,low),high)
        else:
            along=low
        # The user asked for a place the cut cannot reach: flag it so the table
        # can say 已到极限 instead of silently pinning something else.
        clamped=abs(along-wanted)>self.tol
        origin=pivot+normal*along
        faces=self._faces_at(polygon,origin,normal,tangent,size)
        if not faces and high>low:
            # The drag went past the last place the plane still crosses copper
            # (or landed exactly on the feature's end): walk back until the
            # plane really cuts the trace, so a far drag pins the cut as far as
            # it can go instead of quietly reverting to the automatic choice.
            for value in np.linspace(along,low,12):
                origin=pivot+normal*value
                faces=self._faces_at(polygon,origin,normal,tangent,size)
                if faces:along=float(value);break
        if not faces:return None
        nearest=min(float(shapely.distance(shapely.Point(pivot),q)) for q in faces)
        return dict(origin=origin,faces=faces,distance=float(along-extent),span=extent,
                    nearest=nearest,normal=normal,manual=True,clamped=clamped,
                    pivot=pivot,low=low,high=high)

    def _block(self,polygon,cluster,states,owner,others=(),axis_override=None,foreign=None,plane=None):
        """Movable block of one chain: the best single cut beyond its landings.

        The landings of one chain travel together with their die, so the cut is
        taken across the chain direction, past the farthest landing of the chain,
        on the side where the rest of the network leaves the landing: the landings
        and the copper behind the cut move, the network stays. One cut per chain
        means a landing never ends up on the far side of the cut of its neighbour,
        which is what made a cut look far from its landing when a feature carried
        several landings.

        The distance of the cut is the stated step (`_step`: the D setting, else
        2.5 landing diameters), measured from the farthest landing of the chain
        along the network. It is applied to both ends of the chain and the end
        whose seam lies nearer the landings is taken.

        The side is chosen by where the copper is actually crossed near the chain,
        not by the centroid of the whole feature: a feature can wind far away, and
        a cut plane through a far arm would put the seam hundreds of microns from
        the via it belongs to. Crossings far from the chain are ignored for the
        same reason — they are the same net passing by, not the route leaving the
        landing — so only the near crossings become the cut face the seam is
        covered along.
        """
        route=self._chain_axis(polygon,cluster)
        axis=axis_override if axis_override is not None else route
        centres=np.array([v['center'] for v in cluster],float)
        pivot=centres.mean(axis=0)
        coordinates=np.vstack([shapely.get_coordinates(v['land']) for v in cluster])
        if not len(coordinates):return None
        diameter=max(v['land_diameter'] for v in cluster)
        default=self._step(diameter)
        moved_center=np.asarray(self.model.moved(shapely.Point(cluster[0]['center']),owner,states).coords[0],float)
        move=moved_center-np.asarray(cluster[0]['center'],float)
        shift=float(np.linalg.norm(move))
        span=float(np.hypot(polygon.bounds[2]-polygon.bounds[0],polygon.bounds[3]-polygon.bounds[1]))
        size=2.*span+shift+3.*diameter+1.+default
        tangent=np.array([-axis[1],axis[0]])
        towards=float(np.dot(np.asarray(polygon.centroid.coords[0],float)-pivot,axis))
        copper=shapely.get_coordinates(polygon)
        center=self.model.bound.centers[owner]
        probe=max(self.tol*4.,.01)
        manual=None
        if plane is not None:
            manual=self._plane_candidate(polygon,pivot,coordinates,others,size,tangent,plane)
        ends=[manual] if manual is not None else []
        for sign in ((1.,-1.) if manual is None else ()):
            normal=sign*axis
            extent=float(np.max((coordinates-pivot)@normal))
            far=float(np.max((copper-pivot)@normal))
            low=self.tol*4.
            high=far-extent-self.tol*4
            for land in others:
                projected=(shapely.get_coordinates(land)-pivot)@normal
                if projected.size and float(np.max(projected))>extent+low:
                    high=min(high,float(np.min(projected))-extent-low)
            if high<=low:continue
            def probe_at(distance):
                origin=pivot+normal*(extent+distance)
                forward=shapely.intersection(polygon,half_plane(origin,-normal,size))
                if forward.is_empty or forward.area<=self.tol**2:return None
                faces=self._faces_at(polygon,origin,normal,tangent,size)
                if not faces:return None
                nearest=min(float(shapely.distance(shapely.Point(pivot),q)) for q in faces)
                return dict(origin=origin,faces=faces,distance=distance,span=extent,
                            nearest=nearest,normal=normal,pivot=pivot,
                            low=extent+self.tol*4.,high=far-self.tol*4.)
            # Search the cut outward from the landing edge (the overlap) to the
            # next same-net landing or branch node. Among the planes that really
            # cross the copper, take the one where the trace is NARROWEST and
            # locally STRAIGHT: a narrow, square cross-section gives a short seam
            # and a clean cover, and a straight stretch keeps the cut square to
            # both sides of the route.
            candidates=[]
            if high>low:
                width_span=high-low
                sample=max(self.tol*8.,min(diameter*.5,width_span/40.))
                count=int(np.clip(np.ceil(width_span/sample),8,20))
                # An explicit D (the 切口距离 setting) pins the cut; only with
                # D = 0 does the cut search for its own place along the route.
                if self.length_um>self.tol:
                    steps=[default] if low<default<high else []
                else:
                    steps=np.linspace(low,high,count)
                for distance in steps:
                    found=probe_at(float(distance))
                    if found is None:continue
                    found['width']=float(sum(q.length for q in found['faces']))
                    # Shorting another net is a hard failure.  The conflict is
                    # measured lazily in check() below: only the candidates that
                    # can still win are measured, which keeps a big drawing usable.
                    candidates.append(found)
            # Stop at a junction: a branch or a merge with the same-net network
            # doubles the cross-section. Everything past it belongs to the node,
            # not to this route, so the search never cuts beyond that point.
            kept=[];narrow=float('inf')
            for found in sorted(candidates,key=lambda q:q['distance']):
                if found['width']>1.7*narrow+self.tol and kept:break
                narrow=min(narrow,found['width'])
                kept.append(found)
            if kept:
                # The purpose of searching the cut is that the reconstructed
                # chain does not short against another net: only cut positions
                # whose shifted copper and cover keep the spacing rule are
                # acceptable. Among those the narrowest cross-section is the
                # cleanest seam, but it can sit far down a long wide run and drag
                # a huge block along, so the nearest place that already reaches
                # the narrowest width is taken, and among those the straightest.
                # When no position is safe the one with the largest clearance is
                # used anyway and the piece is flagged, because the motion itself
                # brings the copper too close.
                def straight(q):
                    if 'straight' not in q:
                        face=max(q['faces'],key=lambda r:r.length)
                        mid=np.asarray(face.interpolate(.5,normalized=True).coords[0],float)
                        direction=self._local_direction(
                            polygon,mid,max(2.*diameter,float(face.length)))
                        q['straight']=(float(abs(np.dot(direction,axis)))
                                       if direction is not None else 0.)
                    return q['straight']
                def check(q):
                    if 'conflict' not in q:
                        if 'movable' not in q:
                            q['movable']=shapely.intersection(
                                polygon,half_plane(q['origin'],q['normal'],size))
                        q['conflict'],q['gap'],q['foreign']=self._candidate_conflict(
                            q['movable'],q['faces'],center,states[owner],foreign)
                    return not q['conflict']
                # Narrowest cross-section first, but only positions that keep the
                # spacing are eligible: walk the width groups from the narrowest
                # and stop at the first group that has a safe member. Checking a
                # group at a time keeps the cost low — a drawing has hundreds of
                # chains and each candidate otherwise costs a distance query.
                ordered=sorted(kept,key=lambda q:(q['width'],q['distance']))
                groups=[]
                for q in ordered:
                    if groups and q['width']<=groups[-1][0]['width']*1.05+self.tol:
                        groups[-1].append(q)
                    else:groups.append([q])
                best=None;evaluated=0
                for group in groups:
                    safe=[q for q in group if check(q)]
                    evaluated+=len(group)
                    if safe:
                        best=min(safe,key=lambda q:(-round(straight(q),3),q['distance']))
                        break
                    # A big pour yields dozens of width groups and each check
                    # costs a few GEOS predicates; stop early and fall back to
                    # the best clearance that was actually measured.
                    if evaluated>=8:break
                if best is None:
                    # No position can avoid the short (the motion itself brings
                    # the copper too close), so keep the best clearance and mark
                    # the piece; the seam geometry rule still applies.
                    evaluated=0
                    for group in groups:
                        for q in group:check(q)
                        evaluated+=len(group)
                        # A big feature has many candidates; stop once enough of
                        # them are measured, so a drawing stays interactive.
                        if evaluated>=10:break
                    kept=[q for q in kept if 'conflict' in q]
                    best_gap=max(q['gap'] if q['gap'] is not None else float('inf') for q in kept)
                    pool=[q for q in kept
                          if q['gap'] is None or best_gap-float(q['gap'])<=.05]
                    for q in pool:q['conflict']=True
                    target=min(q['width'] for q in pool)
                    waist=[q for q in pool if q['width']<=target*1.05+self.tol]
                    best=min(waist,key=lambda q:(-round(straight(q),3),q['distance']))
                ends.append(best)
        if not ends:
            # No plane along the copper keeps the two pieces apart (a landing
            # larger than the surrounding copper, or a motion that runs straight
            # into the network): the whole feature follows the die, and the table
            # says there is no immovable copper to hold on to.
            normal=axis if towards>self.tol else -axis
            extent=float(np.max((coordinates-pivot)@normal))
            origin=pivot+normal*(extent+default)
            movable=shapely.intersection(polygon,half_plane(origin,normal,size))
            if movable.is_empty or movable.area<=self.tol**2:return None
            conflict,gap,_=self._candidate_conflict(movable,[],center,states[owner],foreign)
            return dict(movable=movable,faces=[],distance=default,span=extent,quality=None,
                        conflict=conflict,gap=gap,
                        plane=(float(normal[0]),float(normal[1]),float(origin[0]),float(origin[1])))
        # The cut goes on the side the network LEAVES the landing: the plane
        # normal must point into the feature mass, otherwise the movable block
        # would swallow the whole route and leave the landing on the far side.
        if manual is None:
            facing=[q for q in ends if np.dot(q['normal'],axis)*towards>=0]
            if facing:ends=facing
        ends.sort(key=lambda q:q['nearest'])
        best=ends[0]
        if 'movable' not in best:
            best['movable']=shapely.intersection(
                polygon,half_plane(best['origin'],best['normal'],size))
        return dict(movable=best['movable'],faces=best['faces'],distance=best['distance'],
                    plane=(float(best['normal'][0]),float(best['normal'][1]),
                           float(best['origin'][0]),float(best['origin'][1])),
                    span=best['span'],conflict=bool(best.get('conflict')),gap=best.get('gap'),
                    manual=bool(best.get('manual')),clamped=bool(best.get('clamped')),
                    window=dict(pivot=[float(best['pivot'][0]),float(best['pivot'][1])],
                                normal=[float(best['normal'][0]),float(best['normal'][1])],
                                low=float(best['low']),high=float(best['high'])))

    def frame(self,states,deformation=None,progress=lambda *_:None,quick=False):
        states=np.asarray(states,float)
        replacements=(deformation or {}).get('replacements',{}) or {}
        fingerprint=tuple((k,tuple(v['indices']),v['geometry'].wkb)
                          for k,items in sorted(replacements.items()) for v in items)
        key=(states.tobytes(),fingerprint)
        hit=self.cache.get(key)
        if hit is not None:return hit
        progress(0,f'重构预演：{len(self.vias)} 个落在固定RDL上的运动过孔')
        groups={}
        for via in self.vias:
            owner=via['owner']
            if owner<0 or not np.any(states[owner]):continue
            for layer,index in via['targets']:
                groups.setdefault((owner,layer,index),[]).append(via)
        features={}
        layer_items={};foreign_trees={}
        for order,((owner,layer,index),vias) in enumerate(sorted(groups.items())):
            polygon=self._copper(layer,index,replacements)
            if polygon is None or polygon.is_empty:continue
            net=int(self.model.nodes[self.model.ids[layer,index]]['net'])
            if layer not in layer_items:
                items=[]
                for i in range(len(self.model.cache.layers[layer]['geometry'])):
                    geometry=self._copper(layer,i,replacements)
                    if geometry is None or geometry.is_empty:continue
                    items.append((int(self.model.nodes[self.model.ids[layer,i]]['net']),i,geometry))
                layer_items[layer]=items
            if (layer,net) not in foreign_trees:
                other=[(n,g) for n,i,g in layer_items[layer] if n!=net]
                foreign_trees[(layer,net)]=(shapely.STRtree([g for n,g in other]),other) if other else (None,[])
            tree,other=foreign_trees[(layer,net)]
            neighbours=(tree,other) if tree is not None else None
            threshold=self._step(max(v['land_diameter'] for v in vias))
            frame=self._own_frame(owner)
            chains=self._clusters(threshold,vias)
            blocks=[]
            for cluster in chains:
                # The cut may search outward only as far as the next landing of
                # the same net: cutting past it would put that landing on the far
                # side of a seam it does not belong to.
                others=[v['land'] for other in chains if other is not cluster for v in other]
                chain=tuple(sorted(int(v['index']) for v in cluster))
                plane=self.cuts.get((layer,index,chain))
                if plane is None and quick:
                    # While the pose animates, keep the cut where the last full
                    # frame put it: re-running the safety search for every
                    # animation step is what made the preview crawl.
                    plane=self._last_planes.get((layer,index,chain))
                # Dragging one chain's cut must not re-run the (expensive)
                # search for every other chain: cache each chain's block by
                # everything that can change its result.
                cache_key=(id(self.model),layer,int(index),int(owner),
                           chain,plane,
                           round(float(states[owner][0]),6),
                           round(float(states[owner][1]),6),
                           round(float(states[owner][2]),9),
                           float(self.length_um),float(self.width_um),float(self.rule_space),
                           float(self.tol),hash(fingerprint))
                entry=self._blocks_cache.get(cache_key)
                if entry is not None and entry[0] is self.model:
                    block=None if entry[1] is None else dict(entry[1])
                else:
                    block=self._block(polygon,cluster,states,owner,others,
                                      foreign=neighbours,plane=plane)
                    if len(self._blocks_cache)>1200:
                        self._blocks_cache={k:v for k,v in self._blocks_cache.items()
                                            if v[0] is self.model}
                    self._blocks_cache[cache_key]=(self.model,None if block is None else dict(block))
                if block is None:continue
                if not quick and block.get('plane'):
                    self._last_planes[(layer,index,chain)]=block['plane']
                block.update(owner=owner,cluster=cluster)
                blocks.append(block)
            if not blocks:continue
            # Copper may follow only the die whose chip frame holds it: one fixed
            # RDL polygon can span several chips, and without this the same copper
            # would be claimed by two dies and move two different ways. Only a
            # feature that actually carries landings in a neighbouring chip is
            # clipped, so a route that merely runs between the chips keeps its
            # shape and its single cut.
            foreign=[q for k,q in self._frames() if frame is None or not q.equals(frame)]
            shared=(frame is not None and bool(foreign)
                    and shapely.intersection(polygon,shapely.union_all(foreign)).area>self.tol**2)
            for block in blocks:
                clip=frame if (shared and all(frame.covers(shapely.Point(v['center']))
                                              for v in block['cluster'])) else None
                block['movable']=self._clip(block['movable'],clip,[v['land'] for v in block['cluster']])
                block['frame']=clip
            blocks=[q for q in blocks if q['movable'] is not None and not q['movable'].is_empty
                    and q['movable'].area>self.tol**2]
            if not blocks:continue
            blocks=self._merge(blocks)
            # One fixed-RDL polygon can carry vias of several dies; every die's
            # blocks belong to that feature, so accumulate instead of replacing.
            feature=features.setdefault((layer,index),dict(original=polygon,blocks=[],foreign=neighbours))
            feature['blocks'].extend(blocks)
            if order%64==0:progress(round(60*order/max(1,len(groups))),f'重构预演：划分移动区 {order}/{len(groups)}')
        pieces=[];out={}
        bridges=0;orphans=0;moved_area=0.;cover_area=0.;max_shift=0.;flush=0;fixed_area=0.;conflicts=0
        for (layer,index),feature in sorted(features.items()):
            original=feature['original'];blocks=feature['blocks']
            net=int(self.model.nodes[self.model.ids[layer,index]]['net'])
            tree,other=feature.get('foreign') or (None,[])
            stay={}
            for owner in sorted({q['owner'] for q in blocks}):
                own=[q['movable'] for q in blocks if q['owner']==owner]
                # Copper another die will move away is not a stationary obstacle
                # for this one: both blocks travel with their own die, so only
                # copper nobody moves can be shorted against.
                elsewhere=[q['movable'] for q in blocks if q['owner']!=owner]
                stay[owner]=shapely.difference(original,shapely.union_all(own+elsewhere))
            feature_moved=[];feature_covers=[]
            # Copper of this trace that stays where it is: the whole network
            # minus every part that follows a die. Drawn warm together with the
            # moved copper and the cover, so the warm colour shows one complete
            # trace (fixed part + shifted part + bridge).
            fixed_part=shapely.difference(original,shapely.union_all([q['movable'] for q in blocks]))
            fixed_area+=float(fixed_part.area)
            for block in blocks:
                owner=block['owner'];center=self.model.bound.centers[owner]
                movable=block['movable'];moved=transform(movable,center,states[owner])
                stationary=stay[owner]
                gap=None;bridge=None;length=None;anchor=None;moved_anchor=None;bridge_area=None
                faces=[]
                if stationary.is_empty:
                    orphans+=1
                else:
                    gap=float(shapely.distance(moved,stationary))
                    # A seam at zero gap is not a defect and no cut position can
                    # open it, because the copper keeps running in the direction
                    # the moved piece travels: the row is reported as 贴合 and the
                    # cover fills the overlap.
                    if gap<=self.tol:flush+=1
                    faces=self._region_faces(movable,original,block.get('faces',[]),block.get('frame'))
                    if faces:
                        # Cover polygon: the four corners of the two cut faces. It
                        # is added for every seam, whatever the gap — skipped only
                        # when the faces already cover it (no crack left).
                        copper=shapely.union_all([stationary,moved])
                        kept=[q for q in bridge_quads(faces,center,states[owner],self.width_um)
                              if shapely.area(shapely.difference(q,copper))>self.tol**2]
                        if kept:
                            bridge=shapely.union_all(kept);bridge_area=float(bridge.area)
                            bridges+=len(kept);cover_area+=bridge_area;feature_covers.append(bridge)
                            face=max(faces,key=lambda q:q.length)
                            anchor=np.asarray(face.interpolate(.5,normalized=True).coords[0],float)
                            moved_anchor=np.asarray(rigid_transform(anchor[None,:],center,states[owner])[0],float)
                            length=float(face.length)
                # Spacing of the reconstructed copper against every other net on
                # the same fixed-RDL layer: the moved block and the cover are new
                # copper, so a distance below the spacing rule (or an overlap) is
                # a short the reconstruction would create.
                clearance=None;conflict=False;conflict_link=None;conflict_net=None
                if tree is not None and other:
                    probe_geometry=shapely.union_all([moved]+([bridge] if bridge is not None else []))
                    which,gap_foreign=self._foreign_gap(probe_geometry,tree,other,self._conflict_limit())
                    if gap_foreign is not None:
                        clearance=float(gap_foreign)
                        conflict=bool(clearance<self.rule_space-self.tol)
                        if which is not None:
                            conflict_net=int(other[which][0])
                            line=shapely.shortest_line(probe_geometry,other[which][1])
                            conflict_link=[[round(float(x),6),round(float(y),6)]
                                           for x,y in shapely.get_coordinates(line)][:2]
                    if conflict:conflicts+=1
                feature_moved.append(moved)
                shift_distance=float(np.linalg.norm(
                    np.asarray(moved.centroid.coords[0])-np.asarray(movable.centroid.coords[0])))
                max_shift=max(max_shift,shift_distance);moved_area+=float(movable.area)
                pieces.append(dict(die=owner,layer=layer,index=index,net=net,movable=movable,moved=moved,
                                   fixed=fixed_part,immovable=stationary,bridge=bridge,cut=shapely.union_all(faces),
                                   vias=[int(v['index']) for v in block['cluster']],
                                   seam=[None if anchor is None else [float(anchor[0]),float(anchor[1])],
                                         None if moved_anchor is None else [float(moved_anchor[0]),float(moved_anchor[1])]],
                                   movable_area_um2=float(movable.area),moved_area_um2=float(moved.area),
                                   fixed_area_um2=float(fixed_part.area),
                                   shift_um=shift_distance,cut_distance_um=block['distance'],
                                   chain_span_um=block['span'],
                                   gap_um=gap,bridge_length_um=length,bridge_area_um2=bridge_area,
                                   clearance_um=clearance,conflict=conflict,
                                   conflict_net=conflict_net,conflict_link=conflict_link,
                                   manual=bool(block.get('manual')),
                                   cut_clamped=bool(block.get('clamped')),
                                   cut_window=block.get('window'),
                                   orphan=stationary.is_empty))
            geometry=shapely.union_all([shapely.difference(original,
                                                          shapely.union_all([q['movable'] for q in blocks])),
                                        *feature_moved,*feature_covers])
            out.setdefault(layer,[]).append(dict(indices=(index,),geometry=geometry,net=net,
                                                 movable=shapely.union_all([q['movable'] for q in blocks]),
                                                 moved=shapely.union_all(feature_moved)))
        # The spacing annotation must describe the RECONSTRUCTED layout, not the
        # nominal one: another net's movable copper travels with its die too, so
        # measuring against where it used to be would put a dimension between two
        # traces that no longer exist. Re-measure every piece against the final
        # geometry of the other nets on its own layer.
        final={}
        for layer,items in layer_items.items():
            replaced={}
            for q in out.get(layer,[]):
                for i in q['indices']:replaced[int(i)]=q['geometry']
            # Every feature of the layer participates, moved or not: a net whose
            # copper nobody moves is still a neighbour that the spacing has to be
            # measured against.
            final[layer]=[(int(n),int(i),replaced.get(int(i),g)) for n,i,g in items]
        trees={}
        for layer,items in final.items():
            for net in {n for n,i,g in items}:
                other=[(n,g) for n,i,g in items if n!=net]
                trees[(layer,net)]=(shapely.STRtree([g for n,g in other]),other) if other else (None,[])
        conflicts=0
        for piece in pieces:
            tree,others=trees.get((piece['layer'],piece['net']),(None,[]))
            piece['clearance_um']=None;piece['conflict']=False
            piece['conflict_net']=None;piece['conflict_link']=None
            if not others or tree is None:continue
            probe=shapely.union_all([piece['moved']]+([piece['bridge']] if piece['bridge'] is not None else []))
            which,gap_foreign=self._foreign_gap(probe,tree,others,self._conflict_limit())
            if gap_foreign is None:continue
            piece['clearance_um']=float(gap_foreign)
            piece['conflict']=bool(gap_foreign<self.rule_space-self.tol)
            if which is not None:
                piece['conflict_net']=int(others[which][0])
                line=shapely.shortest_line(probe,others[which][1])
                piece['conflict_link']=[[round(float(x),6),round(float(y),6)]
                                        for x,y in shapely.get_coordinates(line)][:2]
            if piece['conflict']:conflicts+=1
        pieces.sort(key=lambda q:(q['layer'],-q['movable_area_um2'],q['index'],q['die']))
        result=dict(complete=True,pieces=pieces,replacements=out,scope=SCOPE,
                    parameters=dict(length_um=self.length_um,width_um=self.width_um,overlap_um=self.overlap_um,
                                    landing_diameters=AUTO_LANDING_DIAMETERS),
                    summary=dict(pieces=len(pieces),bridges=bridges,orphans=orphans,flush=flush,conflicts=conflicts,
                                 manual_cuts=len(self.cuts),
                                 rule_space_um=self.rule_space,
                                 movable_area_um2=moved_area,cover_area_um2=cover_area,
                                 fixed_area_um2=fixed_area,max_shift_um=max_shift,
                                 width_um=self.width_um,length_um=self.length_um,
                                 landing_diameters=AUTO_LANDING_DIAMETERS))
        if not np.any(states) and not fingerprint:self.cache.pin(key)
        self.cache[key]=result;progress(100,'重构预演完成')
        return result
