"""Preview the production RDL reconstruction: split, shift, then cover the seam.

Production does not keep the fixed RDL rigid. An operator marks the parts that
stay, the parts that follow a die are shifted onto their vias, and the fissure
left at the seam is covered by a patch of copper.

Software has to guess that split, so this module guesses out loud. For every
moving via whose copper is fixed RDL, the cut is a straight line perpendicular
to the copper that runs through the via — so the cut meets both sides of the
trace at a right angle, exactly how an operator cuts a route.

Landings of the same die that sit close together on one fixed-RDL feature are
one chain: they must travel together, so no cut ever falls between them. Walking
outward from the chain along the route, the copper is measured at every step
(the chord of the plane nearest the chain, not the sum of every arm of the
network that the plane happens to cross). A fan-out is a stretch whose width does
not change from step to step, and a stretch bounded further out by a wider body
is where the copper can be cut without slicing a pad in half: the FIRST cut goes
at its near end and the SECOND cut at its far end, and the whole stretch between
them is given up and replaced by the patch. Both cuts therefore stand on copper
of the same width, and the bridge is that stretch, not just the shift.

When no such stretch exists the search falls back to a single cut a stated
distance past the farthest landing, and when that plane would cross a pour (a
body wider than POUR_WIDTH_UM, with no neck) there is no cut at all: the whole
feature follows its die and whatever natural boundary it has — the chip frame —
becomes the seam. A pour is never split down the middle.

The cover is the polygon formed by the four corners of the two cut faces (the
moved first cut and the second one on the copper that stays), so it spans the
whole stretch instead of just a line across it; it is added whenever a fissure is
left open, whatever the gap is, and skipped only when the two faces already lie
on top of each other, in which case nothing is given up at all.

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
from shapely.prepared import prep
from .session_cache import SessionCache
from .preview import rigid_transform
from .drc_rules import effective_rules

# Distance from the farthest landing of a chain to the cut when no explicit
# distance is set.
AUTO_LANDING_DIAMETERS=2.5
# A movable block thinner than this is a sliver of copper, not a split worth
# making: its cut face is a few microns long and the cover quad swept from it
# reads as a stray bridge hanging off the vacated area, so such blocks stay put.
MIN_BLOCK_UM=5.
# Cover pieces smaller than this are specks left where the copper nearly meets;
# they are invisible at any working zoom and only read as stray marks.
MIN_COVER_UM2=2.
# A bridge shorter than this along the route cannot be built (and reads as a
# crack rather than a patch): a seam that would leave less copper between the
# moved face and the far cut is widened until the cover reaches this length.
MIN_BRIDGE_UM=30.

#ICICICICICICIC
FRAME_MARGIN_UM=10.

# “前后宽度不变” run of its own, and cutting it would put a seam through the
# middle of the metal. Such a body is never split by the automatic search; it
# follows its die whole and the chip frame, if any, becomes the seam.
POUR_WIDTH_UM=120.
# A crossing this much narrower than the widest part of the same route is a
# fan-out neck (a trace leaving a pour), and a neck is cut even when the pour
# around it is wide.
NECK_RATIO=.55

SCOPE=('说明占位')


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
        if not quad.is_valid:
            # A rotated copy of the face turns the four corners into a bowtie.
            # Its convex hull is the envelope the two faces really enclose and,
            # unlike the two triangles make_valid returns, it still touches both
            # sides of the fissure — a bowtie cover is how a rotated die ends up
            # with a seam and no bridge.
            quad=shapely.convex_hull(quad)
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
        # 0 = one cut (the cover is the strip swept by the shift). >0 = a second
        # cut on the fixed side, this far beyond the first: the cover then runs
        # from that new fixed edge to the moved edge, so the bridge is wider by
        # roughly seam length x this distance, and that strip is given up.
        self.extra_um=max(0.,float(getattr(settings,'reconstruction_extra_um',0.)))
        # The cut must sit on a smooth stretch of the route: a seam across a
        # width step (pad entering a trace) leaves a stepped cover, so within
        # +-window the widest-minus-narrowest cross-section must stay below
        # delta. Both are settings because the numbers are process dependent.
        self.smooth_window_um=max(0.,float(getattr(settings,'reconstruction_smooth_window_um',20.)))
        self.smooth_delta_um=max(0.,float(getattr(settings,'reconstruction_smooth_delta_um',5.)))
        self.overlap_um=max(0.,float(getattr(settings,'reconstruction_overlap_um',getattr(settings,'width_overlap_um',0.))))
        # Manual cut planes drawn by the user: (layer, feature, vias) -> plane.
        # A plane pins that chain's cut; the rest of the rule (perpendicular to
        # the route, movable side, cover, spacing check) still applies.
        self.cuts={}
        self.regions=[(str(layer),shapely.box(*bounds),str(role))
                      for layer,bounds,role in (getattr(settings,'reconstruction_regions',()) or ())]
        self._last_planes={}
        for row in getattr(settings,'reconstruction_cuts',()) or ():
            layer,index,vias,nx,ny,ox,oy=row
            self.cuts[(str(layer),int(index),tuple(sorted(int(v) for v in vias)))]=(
                float(nx),float(ny),float(ox),float(oy))
        # Per-chain second cut (the widened bridge's far edge): same key shape as
        # the first, so one chain can have both and dragging the second line only
        # moves that bridge.
        self.extra_cuts={}
        for row in getattr(settings,'reconstruction_extra_cuts',()) or ():
            layer,index,vias,nx,ny,ox,oy=row
            self.extra_cuts[(str(layer),int(index),tuple(sorted(int(v) for v in vias)))]=(
                float(nx),float(ny),float(ox),float(oy))
        # Chains whose cut the user deleted: no split, so no seam and no cover —
        # that stretch of copper simply stays where it is.
        # The GUI pins a cut on a PIECE, whose vias are the union of every chain
        # that merged into it, while cuts are found per chain. Group the rows by
        # feature so a pinned plane still reaches each member chain (otherwise
        # dragging the cut of a wide pour did nothing at all).
        self._cut_rows={}
        for (layer,index,vias),plane in self.cuts.items():
            self._cut_rows.setdefault((layer,index),[]).append((vias,plane))
        self._extra_rows={}
        for (layer,index,vias),plane in self.extra_cuts.items():
            self._extra_rows.setdefault((layer,index),[]).append((vias,plane))
        self.dropped={(str(layer),int(index),tuple(sorted(int(v) for v in vias)))
                      for layer,index,vias in (getattr(settings,'reconstruction_dropped_cuts',()) or ())}
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

        # 固定过孔（不跟 DIE 走）的落盘：一条走线走进固定区时的尽头。
        # 它不进 self.vias（那里只放运动过孔，链只能由运动过孔组成），
        # 单独收一份，用来判断链的终点在哪一端。
        self.stays=[]
        for key,role in model.roles.items():
            if role!='固定过孔':continue
            for index in range(len(model.cache.layers[key]['geometry'])):
                poly=model.nodes[model.ids[key,index]]['poly']
                if poly is None or poly.is_empty:continue
                self.stays.append(dict(key=key, index=index, poly=poly,
                                       net=int(model.nodes[model.ids[key, index]]['net']),
                                       center=np.asarray(poly.centroid.coords[0], float)))

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
    def _minor_width(geometry):
        """Shorter side of the minimum rotated rectangle: how thin a shape is."""
        rectangle=shapely.minimum_rotated_rectangle(geometry)
        coordinates=shapely.get_coordinates(rectangle)
        if len(coordinates)<3:return 0.
        sides=[float(np.linalg.norm(coordinates[i+1]-coordinates[i]))
               for i in range(len(coordinates)-1)]
        return min(sides) if sides else 0.

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

    def _candidate_conflict(self,movable,faces,center,state,foreign,second=None):
        """Whether the shifted piece (plus its cover) reaches another net."""
        if foreign is None:return False,None,None
        tree,nets=foreign
        limit=self._conflict_limit()
        moved=transform(movable,center,state)
        quads=[]
        if second:
            # The bridge of a two-cut seam runs from the moved cut face all the
            # way to the second cut, so the patch is far longer than the block
            # itself: it has to be probed at its real extent, not at the swept
            # hull of the movable copper.
            for face,far in zip([transform(q,center,state) for q in faces],second):
                quad=shapely.Polygon(np.vstack([shapely.get_coordinates(face),
                                                shapely.get_coordinates(far)[::-1]]))
                quads.append(quad if quad.is_valid else shapely.convex_hull(quad))
        elif faces:
            quads=bridge_quads(faces,center,state,self.width_um)
        # Cheap test first: the shifted BOUNDING BOX against the neighbours'
        # boxes.  Most candidates sit far from every other net, and transforming
        # a large polygon just to prove that is what makes a full drawing slow.
        boxes=[moved.bounds]+[q.bounds for q in quads if not q.is_empty]
        box=(min(q[0] for q in boxes),min(q[1] for q in boxes),
             max(q[2] for q in boxes),max(q[3] for q in boxes))
        hits=tree.query(shapely.box(box[0]-limit,box[1]-limit,box[2]+limit,box[3]+limit),
                        predicate='dwithin',distance=limit)
        if not len(hits):return False,float(limit),None
        threshold=self.rule_space-self.tol
        # A cover swept between the two cut faces stays inside the hull of the
        # block and its second cut, so it can come at most ``slack`` closer to a
        # neighbour than the moved copper itself.  A neighbour farther than
        # threshold+slack is therefore safe without building the probe polygons.
        slack=(float(np.linalg.norm([state[0],state[1]])) if quads and not second else 0.)
        probes=[(probe,probe.bounds) for probe in [moved]+quads]
        best=float('inf');which=None
        # On a dense layer the tree can return dozens of neighbours for one
        # candidate; only the nearest few can be the minimum, so rank them by
        # box distance and measure those.
        ranked=sorted((int(i) for i in np.asarray(hits,dtype=int).ravel()),
                      key=lambda i:self._box_gap(box,nets[i][1].bounds))[:8]
        reach=threshold+slack
        for i in ranked:
            geometry=nets[int(i)][1]
            x0,y0,x1,y1=geometry.bounds
            dx=max(x0-box[2],box[0]-x1,0.);dy=max(y0-box[3],box[1]-y1,0.)
            if dx*dx+dy*dy>reach*reach:continue
            # A predicate, not an exact distance: proving "far enough" is the
            # common case and exact distances on thousand-vertex polygons are
            # what dominates a cold frame.
            if not shapely.dwithin(moved,geometry,reach):continue
            for probe,_bounds in probes:
                if not shapely.dwithin(probe,geometry,threshold):continue
                d=float(shapely.distance(probe,geometry))
                if d<best:best=d;which=int(i)
        if best==float('inf'):
            # Nothing inside the rule: report the guaranteed clearance.
            return False,reach,None
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

    def _seams(self, polygon, share, frame=None):
        """The parts of the chip frame that run inside this copper: first cuts.

        Taking the frame's own outline and clipping it with the copper gives
        the cut wherever the copper crosses the chip edge, and nothing else:
        a landing that `_clip` keeps whole can no longer be read as a seam.
        """
        if polygon is None or polygon.is_empty: return []
        if frame is None or frame.is_empty: return []
        edges = [q for q in line_parts(shapely.intersection(frame.boundary, polygon))
                 if q.length > self.tol]
        return edges

    def _join(self,seams):
        groups=[]
        for face in sorted(seams,key=lambda q:-float(q.length)):
            if face is None or face.is_empty: continue
            xy=shapely.get_coordinates(face)
            if len(xy)<2: continue
            ends=[np.asarray(xy[0],float),np.asarray(xy[-1],float)]
            unit=ends[1]-ends[0]
            length=float(np.hypot(*unit))
            if length<=self.tol: continue
            unit=unit/length
            span=sorted(float(p @ unit) for p in ends)
            for group in groups:
                axis=group['unit']
                near=any(float(shapely.distance(face,q))<=MIN_BRIDGE_UM
                         for q in group['faces'])
                same=abs(float(axis @ unit))>=0.97
                if same:
                    mid=0.5*(ends[0]+ends[1])
                    base=group['ends'][0]
                    off=(mid-base)-float((mid-base) @ axis)*axis
                    same=float(np.hypot(*off))<=2.*MIN_BLOCK_UM
                if not (same or near): continue
                if same:
                    gap=max(span[0]-group['span'][1],group['span'][0]-span[1])
                    if gap>MIN_BRIDGE_UM: continue
                group['faces'].append(face)
                group['span']=(min(span[0],group['span'][0]),
                               max(span[1],group['span'][1]))
                break
            else:
                groups.append(dict(unit=unit,ends=ends,faces=[face],
                                   span=(span[0],span[1])))
        merged=[]
        for group in groups:
            pts=[np.asarray(q.coords[i],float) for q in group['faces'] for i in (0,-1)]
            best=None
            for i in range(len(pts)):
                for k in range(i+1,len(pts)):
                    d=float(np.hypot(*(pts[i]-pts[k])))
                    if best is None or d>best[0]: best=(d,pts[i],pts[k])
            if best is not None and best[0]>self.tol:
                merged.append(shapely.LineString([best[1],best[2]]))
        return merged or list(seams)

    def _edges(self,frame):
        """芯片边框拆成一条条边（外框 + 内孔）。"""
        edges=[]
        for ring in [frame.exterior]+list(frame.interiors):
            xy=shapely.get_coordinates(ring)
            for i in range(len(xy)-1):
                if float(np.hypot(*(xy[i+1]-xy[i])))>self.tol:
                    edges.append(shapely.LineString([xy[i],xy[i+1]]))
        return edges

    def _on_edges(self,frame,seams):
        """一条边框边一条刀：把缝归到它所在的那条边，返回 [(边序号, 刀), …]。"""
        seams=[q for q in seams if q is not None and not q.is_empty]
        if frame is None or frame.is_empty or not seams: return []
        edges=self._edges(frame)
        groups={}
        for face in sorted(seams,key=lambda q:-float(q.length)):
            best=None
            for k,edge in enumerate(edges):
                d=float(shapely.distance(face,edge))
                if best is None or d<best[0]: best=(d,k)
            if best is not None and best[0]<=2.*MIN_BLOCK_UM:
                groups.setdefault(best[1],[]).append(face)
        out=[]
        for k,items in sorted(groups.items()):
            pts=[np.asarray(q.coords[i],float) for q in items for i in (0,-1)]
            best=None
            for i in range(len(pts)):
                for j in range(i+1,len(pts)):
                    d=float(np.hypot(*(pts[i]-pts[j])))
                    if best is None or d>best[0]: best=(d,pts[i],pts[j])
            if best is not None and best[0]>self.tol:
                out.append((k,shapely.LineString([best[1],best[2]])))
        return out

    @staticmethod
    def _unit(face):
        xy=shapely.get_coordinates(face)
        vec=np.asarray(xy[-1],float)-np.asarray(xy[0],float)
        size=float(np.hypot(*vec))
        return vec/size if size>1e-9 else np.array([1.,0.])

    def _second_cut(self,polygon,face,moving,frame):
        """The second cut of one first cut: parallel to it, its own width.

        Walks off the cut along the routing until the section stops matching,
        then keeps the line outside the moved frame and at least
        FRAME_MARGIN_UM away from it.
        """
        chord=(face if len(face.coords)<=2
               else shapely.LineString([face.coords[0],face.coords[-1]]))
        if moving is None or moving.is_empty:
            moving = polygon
        # 第一刀先拉成直线（折线取两端连线），把它当成一条无限长直线。
        run = (np.asarray(chord.coords[-1], float)
               - np.asarray(chord.coords[0], float))
        size = float(np.hypot(*run))
        run = run / size if size > self.tol else np.array([1., 0.])
        # 平移方向 = 这条直线的法向，朝“离开移动后铜”的一侧。方向由刀本身
        # 决定，不用 _routing 去猜——芯片边缘的 45° 外扩边会被误当成走线方向，
        # 于是整条搜索线斜着穿出走线。用刀的法向，两刀必然平行。
        out = np.array([-float(run[1]), float(run[0])])
        base = np.asarray(chord.centroid.coords[0], float)
        keep = base - np.asarray(moving.centroid.coords[0], float)
        if float(np.dot(out, keep)) < 0.:
            out = -out
        wide = float(chord.length)  # 第一刀长度 = 缝处走线宽度
        # 第二刀：把第一刀拉成直线后当成无限长直线，沿它的法向 1 µm 一步向外
        # 平移；每一步取这条线与铜的交段里离第一刀最近的那一块（= 同一条走线），
        # 直到落在移动后边框之外且离框 ≥ FRAME_MARGIN_UM，再按线宽突变收刀。
        reach = self._step(30.) * 4.
        last = None
        far = None
        far_ok = None
        hit = base
        for jump in range(1, int(reach) + 1):
            shift = out * float(jump)
            line = shapely.LineString([base - run * reach + shift,
                                       base + run * reach + shift])
            pts = sorted(shapely.get_coordinates(
                shapely.intersection(polygon.boundary, line)),
                key=lambda p: float(np.dot(np.asarray(p, float), run)))
            best = None
            for a_, b_ in zip(pts, pts[1:]):
                span = shapely.LineString([a_, b_])
                size_ = float(span.length)
                if size_ <= self.tol or size_ > 3. * wide:
                    continue
                mid = shapely.Point(span.interpolate(.5))
                if not polygon.covers(mid):
                    continue
                gap = float(np.hypot(float(mid.x) - float(base[0]),
                                     float(mid.y) - float(base[1])))
                if best is None or gap < best[0]:
                    best = (gap, size_, span)
            if best is None:
                continue
            _, size_, span = best
            far = span
            hit = np.asarray(span.interpolate(.5).coords[0], float)
            clear = (frame is None or frame.is_empty
                     or ((not bool(frame.covers(span)))
                         and float(shapely.distance(span, frame.boundary))
                         >= FRAME_MARGIN_UM))
            if far_ok is None:
                if not clear:
                    continue
                far_ok = span
                last = (size_, run)
                continue
            if last is not None and abs(size_ - last[0]) > self.smooth_delta_um:
                break
            last = (size_, run)
            far_ok = span
        if far_ok is not None:
            far = far_ok
        if far is not None:
            # 终选：绕该处断面的中点小幅旋转，取“交点线段最短”的那个角度，
            # 也就是垂直于这一段走线的切法。旋转无效（找不到更短的）就保持平行。
            origin_ = far.interpolate(.5)
            best_turn = None
            for turn in range(-8, 9):
                angle = np.radians(5. * turn)
                cos_, sin_ = np.cos(angle), np.sin(angle)
                axis = np.array([cos_ * run[0] - sin_ * run[1],
                                 sin_ * run[0] + cos_ * run[1]])
                line = shapely.LineString([
                    (float(origin_.x) - axis[0] * reach,
                     float(origin_.y) - axis[1] * reach),
                    (float(origin_.x) + axis[0] * reach,
                     float(origin_.y) + axis[1] * reach)])
                pts = sorted(shapely.get_coordinates(
                                 shapely.intersection(polygon.boundary, line)),
                             key=lambda p: float(np.dot(np.asarray(p, float), axis)))
                for a_, b_ in zip(pts, pts[1:]):
                    span = shapely.LineString([a_, b_])
                    size_ = float(span.length)
                    if size_ <= self.tol or size_ > 3. * wide:
                        continue
                    mid = shapely.Point(span.interpolate(.5))
                    if not polygon.covers(mid):
                        continue
                    if float(shapely.distance(mid, origin_)) > max(4. * self.tol, 1.):
                        continue
                    if best_turn is None or size_ < best_turn[0]:
                        best_turn = (size_, span)
            if best_turn is not None and best_turn[0] < float(far.length) * 0.98:
                far = best_turn[1]
                hit = np.asarray(far.interpolate(.5).coords[0], float)
        if far is None:
            return None

        if not hasattr(self, '_seam_debug'):
            self._seam_debug = []
        self._seam_debug.append(
            'h=(%.1f,%.1f)%s o=(%.2f,%.2f) r=(%.2f,%.2f) w=%.1f '
            'f=%.1f[(%.1f,%.1f)-(%.1f,%.1f)] %s'
            % (float(hit[0]), float(hit[1]),
               '' if polygon.covers(shapely.Point(float(hit[0]), float(hit[1])))
               else '!OFF',
               float(out[0]), float(out[1]), float(run[0]), float(run[1]),
               wide, float(far.length),
               far.coords[0][0], far.coords[0][1],
               far.coords[-1][0], far.coords[-1][1],
                'last=none' if last is None else 'last=%.1f' % float(last[0])))

        return far

    def _section(self, polygon, centre, run, reach):
        """The chord of the copper cut square to `run` through `centre`."""
        point = shapely.Point(float(centre[0]), float(centre[1]))
        probe = shapely.LineString([(centre[0] - run[0] * reach, centre[1] - run[1] * reach),
                                    (centre[0] + run[0] * reach, centre[1] + run[1] * reach)])
        parts = [q for q in line_parts(shapely.intersection(polygon, probe))
                 if q.length > self.tol]
        if not parts: return None
        best = min(parts, key=lambda q: shapely.distance(q, point))
        # A chord that does not run through the centre is a neighbour's half
        # chord picked up at a corner: it would be shorter than one width and
        # would not reach both sides of this trace.
        if float(shapely.distance(best, point)) > max(self.tol * 4, 0.5):
            return None
        return best

    def _walk_second(self, polygon, face, outward, step=1., reach=None, tol_deg=12.):
        """Walk one micron at a time and cut the trace square to its run.

        Every step re-measures the cross-section and takes its own direction as
        the next marching direction, so a bending trace is still cut square.
        The walk stops where the width or the heading jumps: that is the far
        cut of this stretch.
        """
        if (polygon is None or polygon.is_empty
                or face is None or face.is_empty): return None
        normal = np.asarray(outward, float)
        span = float(np.hypot(*normal))
        if span <= self.tol: return None
        normal /= span
        eyes = shapely.get_coordinates(face)
        if len(eyes) < 2: return None
        run = np.asarray(eyes[-1] - eyes[0], float)
        if float(np.hypot(*run)) <= self.tol: return None
        run /= float(np.hypot(*run))
        if abs(float(np.dot(run, normal))) > .4:  # not a cross-section
            run = np.array([-normal[1], normal[0]])
        width = float(face.length)
        centre = np.asarray(face.centroid.coords[0], float)
        limit = float(reach if reach is not None else self._step(30.) * 4.)
        probe_reach = limit * 4.
        best = None
        heading = run
        for _ in range(int(limit / step) + 1):
            centre = centre + normal * step
            chord = self._section(polygon, centre, heading, probe_reach)
            if chord is None: break
            if float(shapely.distance(chord,
                                      shapely.Point(float(centre[0]), float(centre[1])))) > max(step * 2.,
                                                                                                self.tol * 4):
                break  # the trace ran out
            if abs(float(chord.length) - width) > self.smooth_delta_um:
                break  # width jumps
            turn = shapely.get_coordinates(chord)
            heading = np.asarray(turn[-1] - turn[0], float)
            heading /= float(np.hypot(*heading))
            normal = np.array([heading[1], -heading[0]])  # square to the new chord
            if float(np.dot(normal, outward)) < 0: normal = -normal
            if float(np.degrees(np.arccos(np.clip(float(np.dot(normal, outward)), -1., 1.)))) > tol_deg:
                break  # heading jumps: a corner
            width = float(chord.length)
            centre = np.asarray(chord.centroid.coords[0], float)
            best = chord
        if best is None: return None
        # Settle: a tilted probe cuts the trace obliquely and its ends miss the
        # two sides, so the chord is re-measured with its own direction until the
        # two agree - then it is square to the run and exactly one width long.
        for _ in range(6):
            chord = self._section(polygon, centre, heading, probe_reach)
            if chord is None: break
            turn = shapely.get_coordinates(chord)
            fresh = np.asarray(turn[-1] - turn[0], float)
            if float(np.hypot(*fresh)) <= self.tol: break
            fresh /= float(np.hypot(*fresh))
            if abs(float(np.dot(fresh, heading))) > .9995:
                best = chord;
                break
            heading = fresh
            centre = np.asarray(chord.centroid.coords[0], float)
            best = chord
        turn = shapely.get_coordinates(best)
        if float(np.dot(np.asarray(turn[-1] - turn[0], float), run)) < 0:
            best = shapely.LineString(turn[::-1])
        return best

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
                               pairs=[p for q in items for p in (q.get('pairs') or ())],
                               second=next((q.get('second') for q in items
                                            if q.get('second') is not None),None),
                               distance=max(q['distance'] for q in items),
                               span=max(q['span'] for q in items),
                               owner=items[0]['owner'],frame=items[0].get('frame'),
                               conflict=any(bool(q.get('conflict')) for q in items),
                               manual=any(bool(q.get('manual')) for q in items),
                               manual_lost=any(bool(q.get('manual_lost')) for q in items),
                               clamped=any(bool(q.get('clamped')) for q in items),
                               window=next((q['window'] for q in items if q.get('window')),None),
                               gap=min((q['gap'] for q in items if q.get('gap') is not None),
                                       default=None),
                               cluster=[v for q in items for v in q['cluster']],
                               seam=any(bool(q.get('seam')) for q in items),
                               follows=any(bool(q.get('follows')) for q in items)))
        unique = []
        for region in merged:
            faces = []
            for face in region['faces']:
                if all(float(shapely.distance(face, q)) > self.tol * 4 for q in faces):
                    faces.append(face)
            pairs = []
            for face, far in (region.get('pairs') or ()):
                if not any(float(shapely.distance(face, q)) <= self.tol * 4 for q in faces):
                    continue
                if any(float(shapely.distance(far, q)) <= self.tol * 4 for _, q in pairs):
                    continue
                pairs.append((face, far))

            region['faces'] = faces
            region['pairs'] = pairs
            unique.append(region)
        return unique

    def _step(self,diameter):
        """Stated distance from the farthest landing of a chain to the cut."""
        return self.length_um if self.length_um>0. else AUTO_LANDING_DIAMETERS*diameter

    def _chain_axis(self, polygon, cluster):
        """Direction through a chain of landings, else the local copper."""
        if len(cluster) > 1:
            centres = np.array([v['center'] for v in cluster], float)
            gaps = np.linalg.norm(centres[:, None, :] - centres[None, :, :], axis=2)
            first, second = np.unravel_index(int(np.argmax(gaps)), gaps.shape)
            delta = centres[second] - centres[first];
            span = float(np.linalg.norm(delta))
            if span > self.tol: return delta / span
        return self._axis(polygon, cluster[0]['land'])

    def _stay_ends(self, layer, index, polygon, frame, limit=None):
        """这块铜盖住的固定孔，先按孔距并成若干“孔区域”
        （A-B 近、B-C 近即 A/B/C 同区，不看 A-C），
        每个区域取左上角那颗孔的中心作代表；代表按 从左到右、从上到下 排序。"""
        prep = shapely.prepared.prep(polygon)
        if limit is None:
            limit = self._step(30.)
        holes = []
        for stay in self.stays:
            if not prep.intersects(stay['poly']): continue
            area = float(stay['poly'].area)
            if area <= 0.: continue
            if float(shapely.intersection(polygon, stay['poly']).area) < area * .5: continue
            holes.append(([float(stay['center'][0]), float(stay['center'][1])], stay['poly']))
        # 并查集：孔距 ≤ limit 就连成一区
        parent = list(range(len(holes)))

        def root(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        for a in range(len(holes)):
            for b in range(a + 1, len(holes)):
                if shapely.distance(holes[a][1], holes[b][1]) <= limit:
                    ra, rb = root(a), root(b)
                    if ra != rb: parent[rb] = ra
        regions = {}
        for i in range(len(holes)):
            regions.setdefault(root(i), []).append(holes[i][0])
        picks = []
        for members in regions.values():
            top_left = min(members, key=lambda p: (round(p[0], 1), -round(p[1], 1)))
            picks.append(top_left)
        picks.sort(key=lambda p: (round(p[0], 1), -round(p[1], 1)))
        outer = []
        if frame is not None and not frame.is_empty:
            probe = frame.buffer(self.tol * 4)
            outer = [p for p in picks if not probe.covers(shapely.Point(p[0], p[1]))]
        return picks, outer
    @staticmethod
    def _oriented(chain,anchors):
        """Reverse a chain so its terminal end is the one next to a fixed landing."""
        if len(chain)<2:return chain
        def gap(v):
            x,y=v['center']
            return min((x-a)**2+(y-b)**2 for a,b in anchors)

        if gap(chain[0]) > gap(chain[-1]): chain.reverse()
        return chain

    def _clusters(self, threshold, vias, polygon=None, frame=None):

        # route划分
        # 一块孤立的铜（polygon）可能有多个尽头，每个尽头都有过孔落盘。
        # 两片落盘只有在“它们之间的直线一直落在这一块铜上”时才相连，所以图
        # 顺着走线生长，不会跨过缝跳到相邻的走线；从一条尽头一直走到另一条
        # 尽头就是一条链。走到分支（还有未访问的邻接）就收尾，下一条链从分支
        # 另一侧继续，因此每个落盘只属于一条链。
        count = len(vias)
        if count < 2: return [list(vias)]
        neighbours = [set() for _ in range(count)]
        for a in range(count):
            for b in range(a + 1, count):
                if polygon is None:
                    if shapely.distance(vias[a]['land'], vias[b]['land']) > threshold: continue
                else:
                    run = shapely.LineString([list(vias[a]['center']), list(vias[b]['center'])])
                    if not polygon.covers(run): continue
                neighbours[a].add(b);
                neighbours[b].add(a)
        if not any(neighbours): return [sorted(vias, key=lambda v: v['index'])]
        taken = set();
        chains = []
        while True:
            free = [i for i in range(count) if i not in taken]
            if not free: break
            # 从“尽头”起步：优先选只有一个邻居、且紧邻已访问铜的那个落盘，
            # 这样新链从上一段停下处接着走，不会跳到无关的另一段。
            start = [i for i in free if len(neighbours[i]) <= 1 and any(k in taken for k in neighbours[i])]
            if not start:
                start = [i for i in free if len(neighbours[i]) <= 1]
            if not start:
                start = [i for i in free if any(k in taken for k in neighbours[i])]
            if not start: start = free
            walk = [min(start, key=lambda i: (len(neighbours[i]), i))]
            taken.add(walk[0])
            while True:
                steps = [k for k in sorted(neighbours[walk[-1]]) if k not in taken]
                if not steps: break
                taken.add(steps[0]);
                walk.append(steps[0])
            chains.append(walk)
            # 一条链有两处尽头：起点必须是“移动落盘”（在芯片边框内，铜跟着这颗 DIE 走），
            # 终点尽头分两种：落在芯片边框外的固定区落盘优先，其次是链条另一端。
            # DFS 走法本身不管方向，所以只做一次翻转：只有“起点在框外、终点在框内”
            # 才需要反过来，其余情况（两端都在框内、两端都在框外）保持原样。
            if frame is not None:
                x0, y0, x1, y1 = frame.bounds
                for chain in chains:
                    if len(chain) < 2:
                        continue
                    head = vias[chain[0]]['center'];
                    tail = vias[chain[-1]]['center']
                    head_in = x0 <= head[0] <= x1 and y0 <= head[1] <= y1
                    tail_in = x0 <= tail[0] <= x1 and y0 <= tail[1] <= y1
                    if not head_in and tail_in:
                        chain.reverse()
        return [[vias[i] for i in chain] for chain in chains]

    @staticmethod
    def _face_outward(face,movable,normal=None):
        """Direction from the movable copper into the copper that stays."""
        if normal is not None:
            return np.asarray(normal,float)
        vector=np.asarray(face.centroid.coords[0],float)-np.asarray(movable.centroid.coords[0],float)
        length=float(np.hypot(*vector))
        return vector/length if length>0 else np.array([1.,0.])

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
        inner=[]
        for piece in line_parts(shapely.intersection(line,polygon)):
            if piece.length<=self.tol:continue
            mid=np.asarray(piece.interpolate(.5,normalized=True).coords[0],float)
            if (shapely.contains(polygon,shapely.Point(mid-normal*probe))
                    and shapely.contains(polygon,shapely.Point(mid+normal*probe))):
                faces.append(piece)
            else:
                inner.append(piece)
        # A wide pour often has copper on only one side of the cut near its
        # border (or the plane runs along an edge), and a hand-placed cut there
        # used to be rejected as “no face”, so the split silently jumped back to
        # the automatic place. Any real crossing is a seam; prefer the
        # both-sides pieces but never throw the drag away when the plane does
        # cross copper.
        return faces or inner

    @staticmethod
    def _safe_difference(first,second):
        """difference() that survives the slivers a near-degenerate ring makes."""
        try:
            return shapely.difference(first,second)
        except Exception:
            pass
        try:
            first=shapely.make_valid(first);second=shapely.make_valid(second)
            return shapely.difference(first,second)
        except Exception:
            return shapely.Polygon()

    @staticmethod
    def _areas(geometry):
        """Polygon parts of a geometry: a pinched ring leaves stray lines."""
        if geometry is None:return shapely.Polygon()
        parts=[q for q in shapely.get_parts(geometry)
               if q.geom_type=='Polygon' and q.area>1e-9]
        return shapely.union_all(parts) if parts else shapely.Polygon()

    @staticmethod
    def _outline(copper,window,tol=.001):
        """Longest run of a copper edge inside a window, as a line."""
        edge=shapely.intersection(copper.boundary,window)
        parts=[q for q in shapely.get_parts(edge)
               if q.geom_type=='LineString' and q.length>tol*4.]
        return max(parts,key=lambda q:q.length) if parts else None

    @classmethod
    def _routing(cls,face,copper,direction,tol=.001):
        """Direction the copper runs in where ``face`` cuts across it.

        A patch that grows along the cut's own normal leaves a trace that the
        cut crosses at an angle: the two long edges then sit at an angle to the
        copper's edges, so the far end pokes out over bare substrate.  Growing
        along the copper's own edge keeps the patch in the trace.
        """
        best=None
        score=0.
        reach=max(float(face.length)*.25,tol*4.)
        for point in (shapely.Point(shapely.get_coordinates(face)[0]),
                      shapely.Point(shapely.get_coordinates(face)[-1])):
            # At each end of the cut the copper's own edge carries on: that run
            # is the direction the trace leaves the seam in.
            run=cls._outline(copper,shapely.buffer(point,reach))
            if run is None:continue
            xy=shapely.get_coordinates(run)
            vec=np.asarray(xy[-1],float)-np.asarray(xy[0],float)
            length=float(np.linalg.norm(vec))
            if length>score:score=length;best=vec/length
        if best is None:return np.asarray(direction,float)
        if float(np.dot(best,np.asarray(direction,float)))<0.:best=-best
        return best

    def _width_window(self,polygon,origin,normal,tangent,size,half=None):
        """Width swing of the route within +-half along the cut normal."""
        half=self.smooth_window_um if half is None else float(half)
        if half<=self.tol:return 0.
        widths=[]
        for offset in np.linspace(-half,half,9):
            faces=self._faces_at(polygon,origin+normal*float(offset),normal,tangent,size)
            if not faces:continue
            widths.append(float(sum(q.length for q in faces)))
        if len(widths)<2:return 0.
        return float(max(widths)-min(widths))

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
        # The window is expressed the way the GUI drags it: the landing edge
        # (distance 0) to the end of the copper, so the overlay clamp and the
        # committed position can never disagree by a tolerance again.
        low=extent
        high=far-self.tol*4.
        along=float(np.dot(origin-pivot,normal))
        wanted=along
        if high>low:
            along=min(max(along,low+self.tol*4.),high)
        else:
            along=low+self.tol*4.
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
        swing=self._width_window(polygon,origin,normal,tangent,size)
        return dict(origin=origin,faces=faces,distance=float(along-extent),span=extent,
                    nearest=nearest,normal=normal,manual=True,clamped=clamped,
                    variation=swing,taper=swing>self.smooth_delta_um+self.tol,
                    pivot=pivot,low=low,high=high)

    def _width_profile(self,polygon,pivot,normal,tangent,size,extent,high,diameter):
        """Width of the route along the cut normal, sampled outward from a chain.

        Only the crossing NEAREST the chain is the route's own width. A feature
        can pass by hundreds of microns away (the same net winding back, or a
        pour beside a trace), and adding up every chord of the plane reported
        that distant arm instead: the route then looked like a wide body and the
        cut landed in the middle of the metal. On the near crossing the measured
        width changes exactly where the copper does.

        Returns the samples and how the walk ended: ``'junction'`` (the copper
        changed to a wider body), ``'empty'`` (no copper on the plane) or
        ``'high'`` (the search bound). A stretch that ends at a junction is a
        fan-out tail bounded by the body it joins; one that ends at the bound of
        the search just keeps its width to the end of the copper and has no tail.
        """
        span=high-extent
        if span<=self.tol*8.:return [],''
        step=max(self.tol*8.,max(diameter*.5,6.),span/32.)
        point=shapely.Point(float(pivot[0]),float(pivot[1]))
        samples=[]
        narrow=float('inf')
        mark=extent
        stop='high'
        while mark<=high+self.tol:
            origin=pivot+normal*mark
            faces=self._faces_at(polygon,origin,normal,tangent,size)
            if not faces:stop='empty';break
            face=min(faces,key=lambda q:float(shapely.distance(point,q)))
            width=float(face.length)
            if width<=self.tol:stop='empty';break
            # A junction (a branch or a merge with the same net) doubles the
            # cross-section: everything past it belongs to the node, not to this
            # route, so the walk stops before it.
            if samples and width>1.7*narrow+self.tol:stop='junction';break
            narrow=min(narrow,width)
            samples.append((float(mark),width,face))
            mark+=step
        return samples,stop

    def _flat_runs(self,samples):
        """Stretches where the cross-section does not change: 前后宽度不变处.

        A stretch keeps the min and the max width it has seen and ends as soon as
        they drift apart by more than the smooth delta: comparing each sample
        with the previous one would let a slow taper creep into one long “flat”
        run, and the two cut faces of a bridge would then differ in width.
        """
        runs=[]
        for mark,width,face in samples:
            if runs:
                run=runs[-1]
                low=min(run['low'],width);high=max(run['high'],width)
                if high-low<=self.smooth_delta_um+self.tol:
                    run['end']=mark;run['low']=low;run['high']=high
                    run['samples'].append((mark,width,face))
                    continue
            runs.append(dict(start=mark,end=mark,low=width,high=width,width=width,
                             samples=[(mark,width,face)]))
        return runs

    def _tail_cut(self,polygon,pivot,normal,tangent,size,extent,high,diameter,point,clip):
        """One plane past the farthest landing, for a route without a fan-out.

        A trace that keeps its width from the landing to the end of the copper
        has no stretch to give up, so the seam stays where it always was: a hair
        past the landings, or exactly D when the operator set one. It still
        walks outward to a place whose cross-section does not swing, and it still
        refuses a wide body: a plane crossing a pour would draw a seam across the
        metal, so there the feature simply follows the die whole.
        """
        span=high-extent
        if span<=self.tol*8.:return None
        pinned=self.length_um>self.tol
        want=extent+(self.length_um if pinned else self.tol*4.)
        step=max(self.tol*8.,min(6.,diameter*.5))
        distance=want
        faces=[]
        # An explicit D is the operator's distance: it is not walked outward to
        # the next place the width happens to be steady, only refused when the
        # plane crosses a pour or nothing at all.
        while distance<=high+self.tol:
            origin=pivot+normal*distance
            if clip is not None and not clip.covers(shapely.Point(origin)):return None
            faces=self._faces_at(polygon,origin,normal,tangent,size)
            if faces:
                face=min(faces,key=lambda q:float(shapely.distance(point,q)))
                if float(face.length)>POUR_WIDTH_UM+self.tol:return None
                if pinned or self._width_window(polygon,origin,normal,tangent,size)<=self.smooth_delta_um+self.tol:
                    break
            distance+=step
        if not faces:
            if self.length_um<=self.tol:return None
            # An explicit D past the last copper: the whole feature follows the
            # die and only the distance is reported; there is no seam line.
            return dict(origin=pivot+normal*want,faces=[],pairs=[],second=None,
                        distance=float(want-extent),span=extent,normal=normal,pivot=pivot,
                        variation=0.,low=extent+self.tol*4.,high=high,width=0.,
                        nearest=float('inf'),neck=False)
        return dict(origin=pivot+normal*distance,faces=faces,pairs=[],second=None,
                    distance=float(distance-extent),span=extent,normal=normal,pivot=pivot,
                    variation=self._width_window(polygon,pivot+normal*distance,normal,tangent,size),
                    low=extent+self.tol*4.,high=high,
                    width=float(sum(q.length for q in faces)),
                    nearest=min(float(shapely.distance(point,q)) for q in faces),
                    neck=bool(max(q.length for q in faces)<=NECK_RATIO*POUR_WIDTH_UM),single=True)

    def _band_inside(self,band,ring,steps=4):
        """True when the swept band between two cut faces stays on the copper."""
        coordinates=np.asarray(ring,float)
        for i,j in ((0,3),(1,2)):
            for k in range(steps+1):
                fraction=k/float(steps)
                point=coordinates[i]*(1.-fraction)+coordinates[j]*fraction
                if not band.contains(shapely.Point(float(point[0]),float(point[1]))):
                    return False
        return True

    def _straight_mark(self,polygon,near_face,picked,normal,pivot,clip=None):
        """Farthest cross-section that still lies on the near face's own band.

        The cover of a two-cut seam is the quad swept from the near cut face to
        the far one, so a trace that bends between the two cuts makes that quad
        leave the copper: it cuts the chord of the bend and the patch runs off
        the route.  Only a far cut that keeps the band inside the copper is
        allowed, which puts the far cut before the bend instead of past it.
        """
        if near_face is None or not picked:return None
        tangent=np.array([-float(normal[1]),float(normal[0])])
        start=np.asarray(shapely.get_coordinates(near_face),float)
        if len(start)<2:return None
        start=start[np.argsort(start@tangent)]
        band=prep(polygon.buffer(self.tol*2.))
        far=None
        for mark,width,face in picked:
            coordinates=np.asarray(shapely.get_coordinates(face),float)
            if len(coordinates)<2:break
            coordinates=coordinates[np.argsort(coordinates@tangent)]
            # A different width means the stretch already changed: that end is
            # the width step the two-cut search was looking for, not a bend.
            if abs(float(face.length)-float(near_face.length))>self.smooth_delta_um+self.tol:break
            if clip is not None and not clip.covers(shapely.Point(
                    float(pivot[0])+float(normal[0])*mark,
                    float(pivot[1])+float(normal[1])*mark)):
                break
            ring=np.vstack([start,coordinates[::-1]])
            quad=shapely.Polygon(ring)
            if not quad.is_valid or quad.area<=0.:break
            if not self._band_inside(band,ring):break
            far=float(mark)
        return far

    def _fit_covers(self,quads,copper,stationary,moved,keep_fixed=False):
        """Keep the swept quads that really bridge the fissure.

        The patch must touch both sides: one is the moved copper, the other is
        copper that stays. A wedge that only borders the vacated space beside the
        seam touches one side and is not a fissure. It must also fill most of the
        quad it was swept from -- a wedge left over where the shifted copper only
        grazes the face is the stray triangle that shows up past the copper.
        Measuring against the quad (not seam length x gap) keeps a genuinely
        short seam -- 2/0#43 had a 54 um2 cover against a 56 um2 linear rule and
        lost its bridge entirely.

        ``keep_fixed`` keeps the part of the quad that lies on the copper which
        stays: the seam a shift closed by overlapping has no open space at all,
        and the patch there is a reinforcement strip running outward over that
        copper, not a filling of a crack.
        """
        kept=[]
        for quad in quads:
            if not quad.is_valid:quad=shapely.make_valid(quad)
            piece=(self._safe_difference(quad,moved) if keep_fixed
                   else self._safe_difference(quad,copper))
            if piece.is_empty:continue
            if shapely.distance(piece,stationary)>self.tol*4:continue
            if shapely.distance(piece,moved)>self.tol*4:continue
            if piece.area<max(MIN_COVER_UM2,.1*float(quad.area)):continue
            if 2.*piece.area/max(piece.length,1e-9)<max(10.*self.tol,.2):continue
            kept.append(piece)
        return kept

    # def _seam_patch(self,pairs,center,state,copper):
    def _seam_patch(self, pairs, center, state, moved):

        patches = []
        for face, second in (pairs or ()):
            first = (face if len(face.coords) <= 2 else
                     shapely.LineString([face.coords[0], face.coords[-1]]))
            ring = np.vstack([shapely.get_coordinates(second),
                              shapely.get_coordinates(
                                  transform(first, center, state))[::-1]])
            quad = shapely.Polygon(ring)
            if not quad.is_valid:
                quad = shapely.convex_hull(shapely.MultiPoint(ring))
            patch = self._safe_difference(quad, moved)
            if patch.is_empty or float(patch.area) <= self.tol ** 2:
                continue
            patches.append(patch)
        if not patches:
            return shapely.Polygon()
        return self._areas(shapely.union_all(patches))

    def _outward_toward(self,face,movable,stationary,normal):
        """Direction from a cut face towards the copper that stays.

        The cut's normal points into the movable copper, so translating the face
        along it could push the second cut INTO the moving side: the bridge then
        sat on the wrong side of the seam.  Probe the copper that actually stays
        and flip the direction when the far side is the empty one.
        """
        direction=self._face_outward(face,movable,normal)
        if stationary is None or stationary.is_empty:
            return direction
        point=np.asarray(face.centroid.coords[0],float)
        probe=max(self.tol*4.,1e-2)
        near=float(shapely.distance(stationary,shapely.Point(point+direction*probe)))
        far=float(shapely.distance(stationary,shapely.Point(point-direction*probe)))
        if near>far:
            return -np.asarray(direction,float)
        return direction

    def _far_face(self,line,copper,direction,offset=None):
        """Section of the copper that the patch end actually hands over to.

        The far edge of a patch has to match the copper it hands over to, in
        width AND in centre line.  The edge is taken from the cut face, but two
        things move it away from the copper under it: a patch pushed further out
        (the 30 μm floor, ``extra_um``, a pair face translated along the route)
        lands on a new plane, and the cut itself may still sit a fraction inside
        the wide body where the trace necks down just past it.  The crossing is
        therefore measured a little past the edge (``offset``, one probe step by
        default) and put back on the edge's own plane; the caller keeps it only
        when it is narrower, so a fan-out is never trimmed.
        """
        if line is None or line.is_empty or copper is None or copper.is_empty:
            return None
        coordinates=np.asarray(shapely.get_coordinates(line),float)
        if len(coordinates)<2:
            return None
        delta=coordinates[-1]-coordinates[0]
        span=float(np.linalg.norm(delta))
        if span<=self.tol:
            return None
        tangent=delta/span
        direction=np.asarray(direction,float)
        length=float(np.linalg.norm(direction))
        if length<=self.tol:
            return None
        direction=direction/length
        step=max(4.*self.tol,1.) if offset is None else max(0.,float(offset))
        centre=np.asarray(line.centroid.coords[0],float)+direction*step
        reach=span*1.5+max(8.*self.tol,2.)
        probe=shapely.LineString([centre-tangent*reach,centre+tangent*reach])
        parts=[q for q in line_parts(shapely.intersection(copper,probe))
               if q.length>self.tol]
        if not parts:
            return None
        # Keep the crossing that continues this edge, not a branch standing
        # beside it: the one sharing the most of its length with the edge once
        # the edge is taken to the same plane.
        moved=shapely.affinity.translate(line,xoff=float(direction[0])*step,
                                         yoff=float(direction[1])*step)
        best=max(parts,key=lambda q:float(shapely.intersection(
            q.buffer(self.tol*4.),moved).length))
        return shapely.affinity.translate(best,xoff=float(-direction[0])*step,
                                          yoff=float(-direction[1])*step)


    def _block(self,polygon,cluster,states,owner,others=(),axis_override=None,foreign=None,
               plane=None,extra_plane=None,clip=None):
        """Movable block of one chain: the fan-out stretch two cuts may bound.

        The landings of one chain travel together with their die, so the chain is
        split across its own direction, past its farthest landing, on the side
        where the network leaves it. A bridge is bounded by TWO cuts and both sit
        where the width before and after does not change: the search measures the
        route outward, cuts that measurement into stretches of constant width
        (`_flat_runs`) and takes one stretch — its near end is the cut of the
        moving side, its far end the cut of the copper that stays. Everything
        between the two is given up and covered by the patch, so the bridge spans
        exactly that stretch.

        A wide copper body (a pour) has no such stretch of its own: it is never
        cut. The whole feature then follows the die and the chip frame, if any,
        becomes the seam — that is what keeps a cut line from crossing a pour. A
        narrow neck leaving a pour is cut as usual; it is the fan-out.

        The side is chosen by where the copper is really crossed near the chain,
        not by the centroid of the whole feature: a cut through a far arm would
        put the seam hundreds of microns from the via it belongs to.
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
        point=shapely.Point(float(pivot[0]),float(pivot[1]))
        own = self._own_frame(owner)
        frame = self._own_frame(owner)
        share = self._clip(polygon, frame, [v['land'] for v in cluster])
        # 第一刀只由芯片边框决定：不落在这颗芯片里的铜不切。
        if frame is not None and plane is None and extra_plane is None:
            outside = polygon.area - share.area
            seams = ([] if share.is_empty
                           or outside <= max(self.tol ** 2, 1e-3 * polygon.area)
                     else self._seams(polygon, share, frame))
            if seams:
                seams = sorted(seams, key=lambda q: -float(q.length))
                seams = [q for q in seams if float(q.length) >= MIN_BLOCK_UM] or seams[:1]
                seams = self._join(seams)
                # 第一刀就是缝本身（角落的折线在配对时按两端连线处理）。第二刀
                # 统一在 frame() 里“每条第一刀配一条”，块内不再自己搜，两刀个数
                # 因此严格相等。
                return dict(neck=False, movable=share, faces=seams, pairs=[], second=None,
                            distance=0., plane=None, span=0., conflict=0., gap=0., manual=False,
                            clamped=False, manual_lost=False, taper_um=0., extra_plane=None,
                            tapered=False, follows=False, seam=True,
                            frame_moved=(None if frame is None else
                                         transform(frame, center, states[owner])),
                            window=dict(pivot=np.asarray(pivot, float),
                                        normal=np.asarray(axis, float), low=0., high=0.))
            # 整块在框内（或整块在框外、跟不到这颗芯片）：不落刀，整块随芯片走。
            return dict(neck=False, movable=share, faces=[], pairs=[], second=None,
                        distance=0., plane=None, span=0., conflict=0., gap=0., manual=False,
                        clamped=False, manual_lost=False, taper_um=0., extra_plane=None,
                        tapered=False, follows=True, seam=False,
                        window=dict(pivot=np.asarray(pivot, float),
                                    normal=np.asarray(axis, float), low=0., high=0.))

        manual=None
        if plane is not None:
            manual=self._plane_candidate(polygon,pivot,coordinates,others,size,tangent,plane)
        # A hand-placed cut that crosses no copper cannot be applied; the search
        # takes over again, and the piece says so instead of silently snapping
        # back to where the software had put it.
        lost=bool(plane is not None and manual is None)
        ends=[manual] if manual is not None else []
        for sign in ((1.,-1.) if manual is None else ()):
            normal=sign*axis
            extent=float(np.max((coordinates-pivot)@normal))
            far=float(np.max((copper-pivot)@normal))
            # Both bounds are marks measured from the pivot, like the samples
            # of the profile: the landing edge, then the last place a cut may
            # stand (the copper ends, or the next chain's landing begins).
            low=extent+self.tol*4.
            high=far-self.tol*4.
            for land in others:
                projected=(shapely.get_coordinates(land)-pivot)@normal
                if projected.size and float(np.max(projected))>extent+self.tol*4.:
                    high=min(high,float(np.min(projected))-self.tol*4.)
            if high<=low:continue
            samples,stop=self._width_profile(polygon,pivot,normal,tangent,size,extent,high,diameter)
            if not samples:continue
            body=max(q[1] for q in samples)
            runs=self._flat_runs(samples)
            flat=0
            for order,run in enumerate(runs):
                picked=run['samples']
                # A stretch has to hold two cuts with an unchanged width between
                # them, so a stretch shorter than the avoid window is no bridge.
                if run['end']-run['start']<=max(self.tol*8.,self.smooth_window_um*.5):continue
                # A pour is not a fan-out: its “constant width” is the body
                # itself, and cutting it draws a seam across the middle of the
                # metal. Only a neck that is narrow both absolutely and against
                # the body around it is a fan-out worth cutting.
                if (run['width']>POUR_WIDTH_UM+self.tol
                        and run['width']>NECK_RATIO*body+self.tol):continue
                # The far end of the stretch has to be a real width change: this
                # trace leaves the landings and joins a wider body there. A route
                # that keeps its width right to the end of the copper has no
                # fan-out tail to give up, and giving up the whole route for a
                # few microns of motion is not a bridge.
                wider=(runs[order+1]['low']>run['high']+self.smooth_delta_um+self.tol
                       if order+1<len(runs) else stop=='junction')
                if not wider:continue
                near=picked[0]
                if self.length_um>self.tol:
                    # An explicit D (the 切口距离 setting) pins the first cut
                    # exactly D past the landing edge; the second one stays at
                    # the far end of the same stretch. The sample grid is far
                    # coarser than D, so the plane is measured here instead of
                    # snapping to the nearest sample.
                    want=extent+self.length_um
                    if want<run['start']-self.tol or want>run['end']+self.tol:continue
                    here=self._faces_at(polygon,pivot+normal*want,normal,tangent,size)
                    if not here:continue
                    face_here=min(here,key=lambda q:float(shapely.distance(point,q)))
                    if abs(float(face_here.length)-run['width'])>self.smooth_delta_um+self.tol:continue
                    near=(float(want),float(face_here.length),face_here)
                if picked[-1][0]-near[0]<=max(self.tol*8.,self.smooth_window_um*.5):continue
                origin=pivot+normal*near[0]
                if clip is not None and not clip.covers(shapely.Point(origin)):continue
                faces=[q for q in self._faces_at(polygon,origin,normal,tangent,size)
                       if float(shapely.distance(q,point))<=max(4.*diameter,2.*near[1],self.tol*8.)]
                if not faces:continue
                second=origin
                pairs=[]
                # The far cut has to stay on this trace's own straight band: a
                # bend between the two cuts makes the swept cover leave the
                # copper, so the bridge would run off the route it covers and
                # cut across the bend.  The band test walks the same samples and
                # stops at the last one still on the straight stretch.
                candidates=[q for q in picked if q[0]>near[0]+self.tol*4.]
                far_mark=self._straight_mark(polygon,faces[0],candidates,normal,pivot,clip)
                if far_mark is not None:
                    second=pivot+normal*far_mark
                    reach=float(far_mark-near[0])
                    far_faces=self._faces_at(polygon,second,normal,tangent,size)
                    for face in faces:
                        target=shapely.affinity.translate(
                            face,xoff=float(normal[0])*reach,yoff=float(normal[1])*reach)
                        if not far_faces:break
                        match=min(far_faces,key=lambda q:float(shapely.distance(q,target)))
                        if abs(float(match.length)-float(face.length))>self.smooth_delta_um+self.tol:continue
                        pairs.append((face,match))
                ends.append(dict(origin=origin,faces=faces,distance=float(near[0]-extent),
                                 span=extent,normal=normal,pivot=pivot,
                                 variation=float(run['high']-run['low']),
                                 low=extent+self.tol*4.,high=far-self.tol*4.,
                                 width=float(sum(q.length for q in faces)),
                                 nearest=min(float(shapely.distance(point,q)) for q in faces),
                                 neck=bool(run['width']<=NECK_RATIO*body+self.tol),
                                 second=second,pairs=pairs))
                flat+=1
            if not flat:
                # No stretch of this route is bounded by a wider body: it is a
                # plain trace, and the seam stays a hair past the landings.
                tail=self._tail_cut(polygon,pivot,normal,tangent,size,extent,high,
                                    diameter,point,clip)
                if tail is not None:ends.append(tail)
        if not ends:
            # No stretch of constant width: a pour, a cluster of pads, or a
            # motion that runs straight into the network. Never cut across the
            # body — the whole feature follows the die and whatever boundary it
            # has (the chip frame) becomes the seam.
            normal=axis if towards>self.tol else -axis
            extent=float(np.max((coordinates-pivot)@normal))
            conflict,gap,_=self._candidate_conflict(polygon,[],center,states[owner],foreign)
            return dict(movable=polygon,faces=[],pairs=[],second=None,
                        distance=(default if self.length_um>self.tol else 0.),
                        span=extent,quality=None,conflict=conflict,gap=gap,
                        manual_lost=lost,plane=None,taper_um=0.,tapered=False,
                        neck=False,window=None,manual=False,clamped=False)
        # The cut goes on the side the network LEAVES the landing: the plane
        # normal must point into the feature mass, otherwise the movable block
        # would swallow the whole route and leave the landing on the far side.
        if manual is None:
            facing=[q for q in ends if np.dot(q['normal'],axis)*towards>=0]
            if facing:ends=facing
        def check(q):
            if 'conflict' not in q:
                if 'movable' not in q:
                    q['movable']=shapely.intersection(
                        polygon,half_plane(q['origin'],q['normal'],size))
                q['conflict'],q['gap'],q['foreign']=self._candidate_conflict(
                    q['movable'],q['faces'],center,states[owner],foreign,
                    second=[f for _,f in q.get('pairs') or ()])
            return not q['conflict']
        # A narrow stretch makes a narrow bridge and a short seam, but only a
        # position that keeps the spacing is acceptable; ties go to the stretch
        # nearest the landings.
        safe=[q for q in ends if check(q)]
        pool=safe or ends
        if not safe:
            # No stretch can avoid the short (the motion itself brings the
            # copper too close): keep the widest clearance and mark the piece.
            for q in pool:q['conflict']=True
            best_gap=max(q['gap'] if q['gap'] is not None else float('inf') for q in pool)
            pool=[q for q in pool if q['gap'] is None or best_gap-float(q['gap'])<=.05]
        best=min(pool,key=lambda q:(round(float(q.get('width') or
                                                sum(f.length for f in q['faces'])),3),
                                    float(q.get('nearest') or 0.)))
        # Second cut of this chain, if the user pinned one: the far edge of the
        # widened bridge. It is clamped into the same stretch as the first cut,
        # and only its position matters — the direction stays the cut's own.
        if extra_plane is not None:
            ex,ey,eox,eoy=extra_plane
            en=np.array([float(ex),float(ey)],float)
            length=float(np.linalg.norm(en))
            if length>0.:
                # Only its position matters: the frame puts the second cut on the
                # stationary side of the first, at this signed distance.
                best['extra_plane']=(float(en[0]/length),float(en[1]/length),
                                     float(eox),float(eoy))
        if 'movable' not in best:
            best['movable']=shapely.intersection(
                polygon,half_plane(best['origin'],best['normal'],size))
        return dict(neck=bool(best.get('neck')),movable=best['movable'],faces=best['faces'],
                    pairs=best.get('pairs') or [],second=best.get('second'),
                    distance=best['distance'],
                    plane=(float(best['normal'][0]),float(best['normal'][1]),
                           float(best['origin'][0]),float(best['origin'][1])),
                    span=best['span'],conflict=bool(best.get('conflict')),gap=best.get('gap'),
                    manual=bool(best.get('manual')),clamped=bool(best.get('clamped')),
                    manual_lost=lost,taper_um=float(best.get('variation',0.)),
                    extra_plane=best.get('extra_plane'),
                    tapered=bool(best.get('taper') or
                                 best.get('variation',0.)>self.smooth_delta_um+self.tol),
                    window=dict(pivot=[float(best['pivot'][0]),float(best['pivot'][1])],
                                normal=[float(best['normal'][0]),float(best['normal'][1])],
                                low=float(best['low']),high=float(best['high'])))
    @staticmethod
    def _cover_lookup(exact,rows,layer,index,chain):
        """Row for this chain: its own key first, else a row whose vias contain
        the chain — the GUI pins cuts on merged pieces."""
        if isinstance(exact,(set,frozenset)):
            if (layer,index,chain) in exact:return True
            want=set(chain)
            for other_layer,other_index,vias in exact:
                if (str(other_layer)==str(layer) and int(other_index)==int(index)
                        and want<=set(vias)):return True
            return None
        row=exact.get((layer,index,chain)) if exact else None
        if row is not None:return row
        if rows:
            want=set(chain)
            for vias,value in rows.get((layer,index),()):
                if want<=set(vias):return value
        return None

    def _manual_regions(self,layer,polygon):
        """Manual 动区/静区 boxes of one layer, clipped to this feature."""
        move=[];stay=[]
        for key,box,role in self.regions:
            if key!=layer:continue
            part=shapely.intersection(box,polygon)
            if part.is_empty or part.area<=self.tol**2:continue
            (move if role=='move' else stay).append(part)
        return (shapely.union_all(move) if move else None,
                shapely.union_all(stay) if stay else None)

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
        regions_used=0;regions_unused=0

        anchors_last = 0
        chain_total = 0
        fixed_ends = 0
        fixed_regions = 0
        # fixed_first = None
        fixed_first = None
        fixed_sample = []
        fixed_covered = 0
        fixed_points = set()
        stays_out = 0
        for stay in self.stays:
            x, y = stay['center']
            if not any(f is not None and not f.is_empty and f.contains(shapely.Point(x, y))
                       for _, f in self._frames()):
                stays_out += 1

        layer_items={};foreign_trees={}
        for order,((owner,layer,index),vias) in enumerate(sorted(groups.items())):
            polygon = self._copper(layer, index, replacements)
            original = self._copper(layer, index, {})
            if polygon is None or polygon.is_empty or original is None or original.is_empty: continue
            vias = [v for v in vias if original.covers(v['poly'])]
            if not vias: continue
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

            anchors, anchors_out = self._stay_ends(layer, index, original, frame)
            fixed_covered += len(anchors)
            anchors_last = len(anchors)
            if len(fixed_sample) < 6:
                fixed_sample.append([len(anchors)] + ([round(anchors[0][0], 1), round(anchors[0][1], 1)]
                                                      if anchors else []))
            fixed_regions += len(anchors)
            fixed_first = (layer, index, len(anchors),
                           None if not anchors else [round(anchors[0][0], 1), round(anchors[0][1], 1)])
            # stays_out = 0
            # for stay in self.stays:
            #     x, y = stay['center']
            #     if not any(f is not None and not f.is_empty and f.contains(Point(x, y))
            #                for _, f in self._frames()):
            #         stays_out += 1

            foreign_frames=[q for k,q in self._frames() if frame is None or not q.equals(frame)]
            shared=(frame is not None and bool(foreign_frames)
                    and shapely.intersection(polygon,shapely.union_all(foreign_frames)).area>self.tol**2)
            chains = self._clusters(threshold, vias, polygon, frame)
            if anchors:
                # 走线走进固定区的那一端就是终点，链条从另一端（移动落盘）起步。
                chains=[self._oriented(chain,anchors) for chain in chains]

            chain_total+=len(chains)       # _clusters 的每个元素就是一条走线

            blocks=[]
            for cluster in chains:
                # The cut may search outward only as far as the next landing of
                # the same net: cutting past it would put that landing on the far
                # side of a seam it does not belong to.
                others=[v['land'] for other in chains if other is not cluster for v in other]
                chain=tuple(sorted(int(v['index']) for v in cluster))
                # 只要这块铜一部分在芯片内、一部分在框外，它就必须按芯片边框
                # 下第一刀。原先还要求“孔心全在框内”，跨框的走线孔心常压在框
                # 线上，于是整块退回旧搜索，第二刀就朝“留在原位的那侧”长进了
                # 框里。完全在框内（frame.covers 为真）或完全在框外（与本框交
                # 集为空）都不设 clip，行为不变。
                crosses = (frame is not None and not frame.is_empty
                           and not frame.covers(polygon)
                           and shapely.intersection(polygon, frame).area > self.tol ** 2)
                clip = frame if crosses else None
                plane=self._cover_lookup(self.cuts,self._cut_rows,layer,index,chain)
                if plane is None and quick:
                    # While the pose animates, keep the cut where the last full
                    # frame put it: re-running the safety search for every
                    # animation step is what made the preview crawl.
                    plane=self._last_planes.get((layer,index,chain))
                # Dragging one chain's cut must not re-run the (expensive)
                # search for every other chain: cache each chain's block by
                # everything that can change its result.
                if self._cover_lookup(self.dropped,None,layer,int(index),chain):continue
                extra_plane=self._cover_lookup(self.extra_cuts,self._extra_rows,layer,int(index),chain)
                cache_key=(id(self.model),layer,int(index),int(owner),
                           chain,plane,extra_plane,clip is not None,
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
                                      foreign=neighbours,plane=plane,extra_plane=extra_plane,
                                      clip=clip)
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
            for block in blocks:
                clip=frame if (shared and all(frame.covers(shapely.Point(v['center']))
                                              for v in block['cluster'])) else None
                if (clip is not None and block.get('manual')
                        and block.get('origin') is not None
                        and not clip.covers(shapely.Point(block['origin']))):
                    # The cut was placed outside this die's own chip frame: the
                    # copper may follow the die there, so the frame is no longer
                    # a boundary of the movable block (the cut keeps its place
                    # instead of being clipped away at the frame edge).
                    clip=None
                block['movable']=self._clip(block['movable'],clip,[v['land'] for v in block['cluster']])
                block['frame']=clip
                split = []
                for block in blocks:
                    movable = block['movable']
                    if block.get('seam') and movable is not None and movable.geom_type == 'MultiPolygon':
                        parts = [q for q in movable.geoms if q.area > self.tol ** 2]
                        # A seam belongs to exactly one part: handing it to every part
                        # within tolerance walked the same second cut more than once.
                        kept = {}
                        for face in block['faces']:
                            if not parts: break
                            near = min(parts, key=lambda q: float(shapely.distance(face, q)))
                            if float(shapely.distance(face, near)) <= self.tol * 4:
                                kept.setdefault(id(near), []).append(face)
                        for part in parts:
                            faces = kept.get(id(part)) or []
                            if not faces: continue
                            one = dict(block)
                            one['movable'] = part
                            one['faces'] = faces
                            one['pairs'] = [(f, g) for f, g in (block.get('pairs') or ())
                                            if any(f is h for h in faces)]
                            split.append(one)
                    else:
                        split.append(block)
                blocks = split
            blocks=[q for q in blocks if q['movable'] is not None and not q['movable'].is_empty
                    and q['movable'].area>self.tol**2]
            # Hand-drawn regions are the user's explicit wish; automatic blocks
            # that are only a sliver wide are dropped (the copper stays put), so
            # no cut face and no cover quad is generated there.
            blocks = [q for q in blocks if q.get('region') or q.get('manual') or q.get('seam')
                      or self._minor_width(q['movable']) >= MIN_BLOCK_UM]
            if not blocks:continue
            if not blocks:continue
            blocks=self._merge(blocks)
            if len(blocks)>1:
                def _seam(q):
                    return sum(float(f.length) for f in (q.get('faces') or ()))
                narrow=min(_seam(q) for q in blocks)
                keep=[q for q in blocks if q.get('manual') or q.get('region')
                      or _seam(q)<=narrow*1.5+self.tol]
                if keep:blocks=keep
            move_part,stay_part=self._manual_regions(layer,polygon)
            if move_part is not None or stay_part is not None:
                if stay_part is not None:
                    for block in blocks:
                        block['movable']=shapely.difference(block['movable'],stay_part)
                if move_part is not None:
                    # A 动区 travels with the chain it belongs to: the one whose
                    # movable copper it overlaps, else the nearest chain. A box
                    # drawn over copper without a via still moves — the user said
                    # so — and it never invents a new die.
                    score=lambda q:float(shapely.intersection(move_part,q['movable']).area)
                    best=max(blocks,key=score)
                    if score(best)<=self.tol**2:
                        best=min(blocks,key=lambda q:float(move_part.distance(
                            shapely.union_all([v['land'] for v in q['cluster']]))))
                    best['movable']=shapely.union_all([best['movable'],move_part])
                for block in blocks:
                    must=shapely.union_all([v['land'] for v in block['cluster']])
                    block['movable']=shapely.intersection(
                        shapely.union_all([block['movable'],must]),polygon)
                    if block['movable'].area<=self.tol**2:block['movable']=None
                    # The seam of a hand-drawn region is no longer a plane: take
                    # it from the boundary the movable copper shares with the
                    # copper that stays.
                    block['region']=True;block['faces']=[]
                blocks=[q for q in blocks if q['movable'] is not None]
            # One fixed-RDL polygon can carry vias of several dies; every die's
            # blocks belong to that feature, so accumulate instead of replacing.
            feature = features.setdefault((layer, index), dict(original=polygon, blocks=[], foreign=neighbours))
            for q in blocks:
                q['anchors'] = anchors
                q['anchors_out'] = anchors_out
                q['threshold'] = threshold
            feature['blocks'].extend(blocks)
            if order%64==0:progress(round(60*order/max(1,len(groups))),f'重构预演：划分移动区 {order}/{len(groups)}')
        # Copper that nobody moves, per layer: one fixed-RDL polygon often
        # overlaps another (a wide pour with a narrow trace drawn over it), so a
        # feature-local view counts copper that another feature is about to carry
        # away as stationary — that is what painted a cover triangle between two
        # cut lines and made the strip look like 动区 and 静区 at once.
        layer_moved={}
        for (layer,index),feature in features.items():
            layer_moved.setdefault(layer,[]).extend([q['movable'] for q in feature['blocks']])
        # A block whose movable copper is already carried by another block of the
        # same die adds nothing but a second cut and a thin seam between the two
        # (a narrow trace drawn inside a wide pour). Copper covered elsewhere is
        # not a fissure of its own: drop the redundant block.
        layer_moved={k:shapely.union_all(v) for k,v in layer_moved.items()}
        # 每条第一刀配一条第二刀。配对只由刀本身决定（角落折线按两端连线算），
        # 与块/铜皮无关：一条缝走一次，一条缝只挂到一个块上，所以个数恒等。]\
        seam_cuts = []
        self._seam_debug = []
        for (layer,index),feature in sorted(features.items()):
            polygon=feature['original']
            # 每条第一刀各配一条第二刀。刀要带着它自己那块铜的边框和移动后的铜：
            # 同一块铜横跨两颗芯片时，IC1 的刀不能用 IC2 的边框去量，否则 10 µm
            # 余量、外推方向全错，两条刀会撞在一起。
            # 第一刀清单：每条刀带着自己的块、移动后的边框、移动后的铜。
            cuts = []
            for block in feature['blocks']:
                if not block.get('seam'): continue
                own = self._join([q for q in (block.get('faces') or ())
                                  if q is not None and not q.is_empty])
                block['faces'] = own
                for face in own:
                    cuts.append(dict(face=face,
                                     frame=block.get('frame_moved') or block.get('frame'),
                                     moving=block.get('movable')))
            kept = []
            for cut in cuts:
                unit = self._unit(cut['face'])
                if any(abs(float(unit @ self._unit(q['face']))) >= 0.97
                       and float(shapely.distance(cut['face'], q['face'])) <= MIN_BRIDGE_UM
                       for q in kept):
                    continue
                kept.append(cut)
            cuts = kept
            # 遍历清单：一条第一刀配一条第二刀
            for cut in cuts:
                cut['second'] = self._second_cut(polygon, cut['face'], cut['moving'], cut['frame'])
            if len(cuts) > 1:
                seam_cuts.append('%s#%s×%d[%s]' % (
                    layer, index, len(cuts),
                    ' '.join('(%.0f,%.0f)-(%.0f,%.0f)' % (
                        c['face'].coords[0][0], c['face'].coords[0][1],
                        c['face'].coords[-1][0], c['face'].coords[-1][1])
                             for c in cuts)))
            feature['second_cuts'] = [(c['face'], c['second']) for c in cuts]
            mapping = {id(f): g for f, g in feature['second_cuts']}
            for block in feature['blocks']:
                if not block.get('seam'): continue
                block['pairs'] = [(f, mapping[id(f)]) for f in (block.get('faces') or ())
                                  if id(f) in mapping]
        for (layer,index),feature in list(features.items()):
            keep=[]
            for block in feature['blocks']:
                owner=block['owner'];movable=block['movable']
                others=shapely.union_all([q['movable'] for q in feature['blocks']
                                          if q is not block and q['owner']==owner])
                if others.is_empty:keep.append(block);continue
                rest=float(shapely.difference(movable,others).area)
                if rest<=max(self.tol**2,.01*float(movable.area)):continue
                keep.append(block)
            feature['blocks']=keep
        for (layer,index),feature in list(features.items()):
            if not feature['blocks']:
                features.pop((layer,index))
        used_layers={layer for layer,index in features}
        regions_unused=sum(1 for layer,box,role in self.regions if layer not in used_layers)
        pieces=[];out={}
        bridges=0;orphans=0;moved_area=0.;cover_area=0.;max_shift=0.;flush=0;fixed_area=0.;conflicts=0;lost=0
        tapered_count=0
        for (layer,index),feature in sorted(features.items()):
            original=feature['original'];blocks=feature['blocks']
            net=int(self.model.nodes[self.model.ids[layer,index]]['net'])
            tree,other=feature.get('foreign') or (None,[])
            stay={}
            for owner in sorted({q['owner'] for q in blocks}):
                # Copper that any block of this layer will carry away is not a
                # stationary obstacle: every block travels with its own die, so
                # only copper nobody moves can be shorted against.
                stay[owner]=shapely.difference(original,layer_moved.get(layer))
            feature_moved=[];feature_covers=[]
            # Copper of this trace that stays where it is: the whole network
            # minus every part that follows a die. Drawn warm together with the
            # moved copper and the cover, so the warm colour shows one complete
            # trace (fixed part + shifted part + bridge).
            fixed_part=shapely.difference(original,shapely.union_all([q['movable'] for q in blocks]))
            fixed_area+=float(fixed_part.area)
            # Copper of this trace outside each chip frame, computed once per
            # frame: a frame edge is a real seam only where copper continues out
            # there, otherwise the frame merely clipped the copper.
            outsides={}
            for block in blocks:
                frame=block.get('frame')
                key=None if frame is None else id(frame)
                if key not in outsides:
                    outsides[key]=(None if frame is None or frame.is_empty
                                   else shapely.difference(original,frame))
            for block in blocks:
                owner=block['owner'];center=self.model.bound.centers[owner]
                if block.get('manual_lost'):lost+=1
                if block.get('tapered'):tapered_count+=1
                movable=block['movable'];moved=transform(movable,center,states[owner])
                stationary=stay[owner]
                gap=None;bridge=None;length=None;anchor=None;moved_anchor=None;bridge_area=None
                faces=[];strips=[];extra_cut=None;extra_taper=0.;spans=[]
                if stationary.is_empty:
                    orphans+=1
                else:
                    gap=float(shapely.distance(moved,stationary))
                    # The shift can also close the seam by running the movable
                    # copper ONTO the copper that stays. The two sides then touch
                    # over an area instead of a line: nothing is open, but the
                    # process still wants a patch joining them, so the overlap is
                    # recorded here and covered by an outward strip further down.
                    overlap=0.
                    if moved is not None and not moved.is_empty:
                        try:
                            shared=shapely.intersection(moved,stationary)
                            overlap=0. if shared.is_empty else float(shared.area)
                        except Exception:
                            overlap=0.
                    # A seam at zero gap is not a defect and no cut position can
                    # open it, because the copper keeps running in the direction
                    # the moved piece travels: the row is reported as 贴合 and the
                    # cover fills the overlap.
                    if gap<=self.tol:flush+=1
                    # One part of a seam can be pressed onto the copper that stays
                    # while another part pulls away, and the block distance above
                    # only sees the pressed part.  Such a seam still gets its
                    # second cut, so the patch runs from the moved edge to copper
                    # that really stays instead of ending on the outline the
                    # copper used to fill.
                    pressed=bool(gap<=self.tol and overlap>max(1.,self.tol**2))
                    if block.get('region'):
                        faces=[q for q in line_parts(shapely.intersection(
                            movable.boundary,stationary.boundary))
                            if q.length>self.tol]
                    else:
                        faces=self._region_faces(movable,original,block.get('faces',[]),block.get('frame'))
                    if faces:
                        # Only a face on the final movable boundary is a seam: a
                        # plane that crosses the polygon twice also produces an
                        # interior chord, and sweeping that chord leaves a cover
                        # wedge inside copper that is itself movable — the stray
                        # triangle between two cut lines.
                        edges=movable.boundary
                        faces=[q for q in faces if shapely.distance(q,edges)<=self.tol*4]
                    if faces:
                        # Cover polygon: the four corners of the two cut faces. It
                        # is added for every seam, whatever the gap — skipped only
                        # when the faces already cover it (no crack left).
                        copper=shapely.union_all([stationary,moved])
                        # The cover belongs in the corridor between the moved copper
                        # and the copper that stays. A shift also leaves a strip of
                        # vacated space beside the seam (the block slid sideways),
                        # which touches only one of the two sides — painting that is
                        # the stray triangle beside a pad.
                        corridor=None
                        if gap>self.tol:
                            reach=float(gap)+max(4.*self.tol,.05)
                            corridor=shapely.intersection(shapely.buffer(moved,reach),
                                                          shapely.buffer(stationary,reach))
                        # Only the part of a quad that is really open needs cover,
                        # and it must join the moved copper to copper that stays
                        # behind. A quad swept from a clipped / chip-frame edge has
                        # nothing on the other side, so it would show up as a stray
                        # ribbon far from the seam — drop those.
                        kept=[]
                        # 补线加宽 makes the bridge LONGER without making it
                        # wider: the fixed side is cut a second time, extra_um
                        # further into the copper, and the strip between the two
                        # cuts is given up. The patch then runs from the new fixed
                        # edge to the moved edge — longer in the direction of the
                        # motion, the same width as the trace.
                        # A seam the motion itself closed (the copper already
                        # touches or overlaps) needs no patch: the two sides are
                        # joined by real copper, so nothing is given up and no
                        # strip is invented. It is counted as 贴合 instead.
                        pinned=block.get('extra_plane')
                        # The two-cut seam of an automatic split: the search
                        # already found the far cut of this stretch, so the bridge
                        # runs from the moved cut face to that second cut. A
                        # pinned second cut is the operator's word and takes over
                        # from it; 补线加宽 pushes that same second cut further
                        # out (it does not fall back to a single cut). A seam the
                        # motion already closed gives nothing up, so it keeps no
                        # second cut at all.
                        pair_map = {id(q[0]): q[1] for q in (block.get('pairs') or ())}
                        # A seam block keeps both of its cuts even when the motion
                        # closes the gap: the second one is the grown frame's edge.
                        if pinned or (gap <= self.tol * 4 and not block.get('seam')):
                            pair_map = {}
                        normal=None
                        window=block.get('window')
                        if window:normal=np.asarray(window['normal'],float)
                        strips = []
                        used_outer = []
                        reach=float(np.hypot(original.bounds[2]-original.bounds[0],
                                             original.bounds[3]-original.bounds[1]))+1.
                        if faces:
                            for face in faces:
                                if (block.get('seam') and pair_map
                                        and id(face) not in pair_map):
                                    continue
                                if block.get('seam'):
                                    xy = shapely.get_coordinates(face)
                                    edge = np.asarray(xy[-1], float) - np.asarray(xy[0], float)
                                    edge = edge / max(float(np.hypot(*edge)), 1e-9)
                                    direction = self._outward_toward(
                                        face, movable, stationary,
                                        np.array([-edge[1], edge[0]]))
                                else:
                                    direction = self._outward_toward(
                                        face, movable, stationary, normal)
                                shift = None
                                if pinned:
                                    # This chain's own second cut is an absolute
                                    # plane: the stored point stays where the user
                                    # pinned it, so moving the first cut lengthens
                                    # the bridge instead of sliding it along.
                                    point=np.array([float(pinned[2]),float(pinned[3])],float)
                                    anchor=np.asarray(face.centroid.coords[0],float)
                                    shift=(point-anchor).astype(float)
                                    distance=float(np.linalg.norm(shift))
                                else:
                                    distance=self.extra_um
                                # A patch shorter than MIN_BRIDGE_UM along the
                                # route is not manufacturable. The cover runs from
                                # the moved cut face to the second cut, so the
                                # second cut is pushed out until that run is long
                                # enough; a pinned cut is the operator's word and
                                # stays where it was put.
                                moved_face=transform(face,center,states[owner])
                                # A seam the copper was pressed onto carries its
                                # join along the copper's own run, not along the
                                # cut's normal: the second cut is then laid the
                                # same way, so the patch keeps the trace's width
                                # and its far end stays on the copper.
                                step_direction=(self._routing(face,original,direction)
                                                if pressed and not pinned else
                                                np.asarray(direction,float))
                                swept=abs(float(np.dot(
                                    np.asarray(moved_face.centroid.coords[0],float)
                                    -np.asarray(face.centroid.coords[0],float),
                                    np.asarray(step_direction,float))))
                                # The second cut is placed even when the block's
                                # gap reads as zero: `pressed` says this seam was
                                # closed by running the copper onto the copper
                                # that stays, and it still needs a second cut so
                                # the patch can span from the moved edge to it.
                                if not pinned and (gap>self.tol or pressed):
                                    distance=max(distance,MIN_BRIDGE_UM-swept)
                                if pressed:
                                    # The patch has to run MIN_BRIDGE_UM away
                                    # from the copper that MOVED, and the moved
                                    # edge already sits `swept` out along this
                                    # direction: measure the second cut from it,
                                    # not from the cut face.
                                    distance=max(distance,swept+MIN_BRIDGE_UM)
                                if id(face) in pair_map:
                                    # The second cut of this stretch, found with
                                    # the first one: everything between the two
                                    # is given up and covered.
                                    outer = pair_map[id(face)]
                                    if distance>self.tol and not block.get('seam'):
                                        outer = shapely.affinity.translate(
                                            outer, xoff=float(direction[0]) * distance,
                                            yoff=float(direction[1]) * distance)
                                    # 同一条缝的几条折线腿指向同一条第二刀，只记一次：
                                    # 否则补块与绿线会按腿重复一份。
                                    if block.get('seam'):
                                        if any(outer.equals(q) or
                                               float(shapely.distance(outer, q)) <= self.tol
                                               for q in used_outer):
                                            continue
                                        used_outer.append(outer)
                                else:
                                    if distance<=self.tol:continue
                                    outer=(shapely.affinity.translate(face,xoff=float(shift[0]),
                                                                      yoff=float(shift[1]))
                                           if shift is not None else
                                           shapely.affinity.translate(
                                               face,xoff=float(step_direction[0])*distance,
                                               yoff=float(step_direction[1])*distance))
                                if not pinned:
                                    # The floor is measured on the finished cut:
                                    # a pair face comes from the two-cut search
                                    # and can still sit closer than MIN_BRIDGE_UM
                                    # to the moved face.
                                    span=abs(float(np.dot(
                                        np.asarray(outer.centroid.coords[0],float)
                                        -np.asarray(moved_face.centroid.coords[0],float),
                                        np.asarray(step_direction,float))))
                                    if span<MIN_BRIDGE_UM-self.tol:
                                        push=MIN_BRIDGE_UM-span
                                        pushed=shapely.affinity.translate(
                                            outer,xoff=float(step_direction[0])*push,
                                            yoff=float(step_direction[1])*push)
                                        # Only push while there is copper to land
                                        # on: past the end of the trace a longer
                                        # patch would hang in the air.
                                        if original.buffer(self.tol*2.).covers(pushed):
                                            outer=pushed
                                if not pinned:
                                    # The patch hands over to the copper that
                                    # continues past the far cut.  At a junction
                                    # the cut plane still shows the wide body,
                                    # while the trace that runs on is narrower
                                    # and sits on another centre line: ending on
                                    # the plane's chord leaves the far edge in a
                                    # step above and beside that trace.  Take the
                                    # section of the copper that really continues
                                    # just past the cut instead -- as long as it is
                                    # narrower: the patch keeps the trace's own
                                    # width and never widens over the copper it
                                    # hands over to.
                                    if block.get('seam'):
                                        pass
                                    else:
                                        align = self._far_face(outer, stationary, direction)
                                        if (align is not None
                                                and float(align.length) < float(outer.length) - self.tol
                                                and shapely.distance(align, outer) <= float(
                                                    outer.length) * .5 + self.tol * 4):
                                            outer = align
                                strip=shapely.Polygon(np.vstack([
                                    shapely.get_coordinates(face),
                                    shapely.get_coordinates(outer)[::-1]]))
                                if not strip.is_valid:strip=shapely.make_valid(strip)
                                if strip.area>self.tol**2:strips.append((face,outer,strip))
                        extra_cut=shapely.union_all([q[1] for q in strips]) if strips else None
                        # A pressed seam gives nothing up: the copper between the
                        # two cuts is still there, and the patch is laid on it
                        # (see `keep_fixed` below) instead of replacing it.  Only
                        # an open seam trades that strip for the longer patch.
                        if strips and not pressed and not block.get('seam'):
                            given=shapely.union_all([q[2] for q in strips])
                            stationary=self._safe_difference(stationary,given)
                            if not stationary.is_valid:
                                stationary=shapely.make_valid(stationary)
                            copper=shapely.union_all([stationary,moved])
                            # The second cut can give up the last of the copper
                            # that stays: then nothing is left to bridge, and an
                            # empty geometry would make the distance nan (which
                            # silently poisons the seam width table).
                            gap=(0. if stationary.is_empty or moved.is_empty
                                 else float(shapely.distance(moved,stationary)))
                        frame=block.get('frame')
                        outside=outsides.get(None if frame is None else id(frame))
                        for face in faces:
                            # A face lying on the chip frame is a real seam when this
                            # feature's copper continues on the far side (it belongs
                            # to the neighbouring chip); when nothing continues there
                            # the frame merely clipped the copper, so a quad swept
                            # from it would hang in empty space.
                            if (frame is not None and not frame.is_empty
                                    and shapely.distance(face,frame.boundary)<=self.tol*4):
                                if outside is None or outside.is_empty:continue
                                if shapely.distance(face,outside)>self.tol*4:continue
                            quad_pairs={id(q[0]):q for q in strips}
                            quad_keep=False
                            surrender=None
                            # A seam the motion pressed onto the copper that
                            # stays carries a second cut too (see `pressed`
                            # above): the patch then runs from the moved edge to
                            # that cut, across the copper it landed on, instead
                            # of stopping on the outline the copper used to fill.
                            if (id(face) in quad_pairs
                                    and (gap>self.tol or id(face) in pair_map
                                         or pressed)):
                                # From the new fixed edge to the moved original
                                # edge: the patch covers the wider fissure and
                                # keeps the trace's own width.
                                _,outer,_=quad_pairs[id(face)]
                                quad_keep=pressed
                                moved_face=transform(face,center,states[owner])
                                ring=np.vstack([
                                    shapely.get_coordinates(outer),
                                    shapely.get_coordinates(moved_face)[::-1]])
                                quad=shapely.Polygon(ring)
                                if not quad.is_valid:quad=shapely.convex_hull(quad)
                                # A long second cut makes the four corners span a
                                # bend in the trace, and the quad then wanders off
                                # it or crosses the other nets.  Clipping the swept
                                # strip to this chain's own copper keeps the bridge
                                # on the trace and keeps the polygon simple.
                                quads=[quad]
                                if not quad_keep and not block.get('seam'):
                                    # The operator's second cut sits where the
                                    # copper has to end, often a whole stretch
                                    # further along than the first cut's own
                                    # strip: everything between the two cuts is
                                    # given up, so the copper a bend leaves inside
                                    # the quad belongs to the patch as well --
                                    # otherwise it cuts the patch into two lobes.
                                    surrender=quad
                            else:
                                quads=bridge_quads([face],center,states[owner],self.width_um)
                            before=len(kept)
                            if surrender is None:
                                patch_copper=copper
                                patch_stationary=stationary
                            else:
                                patch_stationary=self._safe_difference(
                                    stationary,surrender)
                                if not patch_stationary.is_valid:
                                    patch_stationary=shapely.make_valid(patch_stationary)
                                patch_copper=shapely.union_all([patch_stationary,moved])
                            fresh=self._fit_covers(quads,patch_copper,patch_stationary,moved,
                                                   keep_fixed=quad_keep)
                            # The swept quad pairs the two cut faces corner to
                            # corner.  When the motion runs mostly along the face
                            # (a short cut at a neck, a diagonal trace) that ring
                            # twists, only a sliver of it stays uncovered, and the
                            # sliver test drops it -- the seam is then left with no
                            # patch at all.  A plain sweep from the face to its
                            # moved copy spans the fissure whatever the motion
                            # direction, so fall back to it.
                            if not fresh and gap > self.tol and not block.get('seam'):
                                fresh = self._fit_covers(
                                    bridge_quads([face], center, states[owner], self.width_um),
                                    copper, stationary, moved)
                            if (not fresh and gap <= self.tol and not pinned
                                    and not block.get('seam')
                                    and overlap > max(1., self.tol ** 2)):
                                # The shift closed the seam by running the movable
                                # copper ONTO the copper that stays: nothing is
                                # open, so there is no crack to fill.  The cover is
                                # then a strip running outward from the moved edge
                                # over that copper, at least MIN_BRIDGE_UM long --
                                # the join the operator expects to see, instead of
                                # two pieces of copper merely touching.
                                need=max(MIN_BRIDGE_UM,self.extra_um)
                                outward=self._outward_toward(
                                    face,movable,stationary,normal)
                                moved_face=transform(face,center,states[owner])
                                # The join is the moved copper's own outline
                                # where it lies against the copper that stays;
                                # a band of `need` around that outline, trimmed
                                # to the staying copper, follows the join in
                                # both width and direction.  Sweeping the cut
                                # face instead only covers one straight strip
                                # beside the pad and leaves the rest of the
                                # joint open.
                                joint=shapely.intersection(
                                    moved.boundary,
                                    stationary.buffer(self.tol*4.+need))
                                if joint.is_empty:
                                    full=shapely.affinity.translate(
                                        moved_face,xoff=float(outward[0])*need,
                                        yoff=float(outward[1])*need)
                                    band=shapely.Polygon(np.vstack([
                                        shapely.get_coordinates(moved_face),
                                        shapely.get_coordinates(full)[::-1]]))
                                    if not band.is_valid:
                                        band=shapely.make_valid(band)
                                else:
                                    band=joint.buffer(need,quad_segs=24)
                                # Only copper that stays can carry the band: the
                                # intersection trims it to that copper's own
                                # shape and keeps its far edge on the copper it
                                # really hands over to.
                                #
                                # The moved copper can overlap on one part of
                                # the seam and pull away on another; the patch
                                # then ends on the slot's far wall and borders
                                # the outline the copper used to fill instead of
                                # the copper that moved.  A patch swept from the
                                # cut face to the edge the copper now shows
                                # closes that slot and ends on that edge, instead
                                # of running on over copper that has left.
                                slot=self._safe_difference(movable,copper)
                                rect=shapely.buffer(face,need,cap_style='flat')
                                near=self._outline(moved,rect)
                                sweep=rect if near is None else shapely.intersection(
                                    rect,shapely.convex_hull(shapely.Polygon(
                                        np.vstack([shapely.get_coordinates(face),
                                                   shapely.get_coordinates(near)]))))
                                cross=self._safe_difference(
                                    shapely.intersection(slot,sweep),moved)
                                on_fixed=self._areas(self._safe_difference(
                                    shapely.union_all([
                                        shapely.intersection(band,stationary),
                                        cross]),moved))
                                if not on_fixed.is_empty:
                                    fresh=self._fit_covers(
                                        [on_fixed],moved,stationary,moved,
                                        keep_fixed=True)
                            kept.extend(fresh)
                            if fresh:
                                # Length of the patch = how far it reaches away
                                # from the copper that moved.  Measuring along a
                                # single face normal only works for the straight
                                # two-cut bridge; a patch that follows a curved
                                # joint reaches the same distance all along it.
                                spans.append(max(
                                    float(shapely.distance(
                                        shapely.Point(xy),moved))
                                    for q in fresh if not q.is_empty
                                    for xy in shapely.get_coordinates(q)))
                        if block.get('seam'):
                            # 缝块只有一个来源：每条缝一个 f(pos1_change, pos2)。
                            seam_patch = self._seam_patch(block.get('pairs'),
                                                          center, states[owner], moved)
                            kept = [] if seam_patch.is_empty else [seam_patch]
                        if kept:
                            bridge = self._areas(shapely.union_all(kept));
                            bridge_area = float(bridge.area)
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
                # Only the seam the search (or the user) placed is a cut. The
                # chip frame edge is a natural boundary: drawing it as a cut
                # would put a cutting line across a pour that merely ends there,
                # which is exactly the line the operator complained about.
                natural=[]
                if frame is not None and not frame.is_empty and not block.get('seam'):
                    natural=[q for q in faces if shapely.distance(q,frame.boundary)<=self.tol*4]
                natural_ids={id(q) for q in natural}
                cuts=[q for q in faces if id(q) not in natural_ids]
                piece_cut=shapely.union_all(cuts) if cuts else None
                piece_frame=shapely.union_all(natural) if natural else None

                # 链的两端仍是运动落盘；这一块铜上若有同网络的固定落盘，
                # 挑离链首最近的一颗作为“走上固定区”的那一端，单独标出来。
                cluster = block['cluster']
                start = [float(q) for q in cluster[0]['center']]
                end = [float(q) for q in cluster[-1]['center']]
                here = block.get('anchors') or ()
                first = block.get('anchors_out') or here
                if here:
                    near = min(first, key=lambda p: min(
                        np.hypot(p[0] - q['center'][0], p[1] - q['center'][1])
                        for q in cluster))
                    if (np.hypot(near[0] - end[0], near[1] - end[1])
                            < np.hypot(near[0] - start[0], near[1] - start[1])):
                        start, end = end, start
                    start = [float(near[0]), float(near[1])]
                    fixed_ends += 1
                    fixed_points.add((round(start[0], 1), round(start[1], 1)))
                end_anchor = None

                pieces.append(dict(die=owner, layer=layer, index=index, net=net, movable=movable, moved=moved,
                                   fixed=fixed_part, immovable=stationary, bridge=bridge, cut=piece_cut,
                                   frame_seam=bool(block.get('seam')), follows=bool(block.get('follows')),
                                   vias=[int(v['index']) for v in block['cluster']],
                                   chain_ends=[start, end],
                                   chain_anchor=end_anchor,
                                   seam=[None if anchor is None else [float(anchor[0]), float(anchor[1])],
                                         None if moved_anchor is None else [float(moved_anchor[0]),
                                                                            float(moved_anchor[1])]],
                                   movable_area_um2=float(movable.area), moved_area_um2=float(moved.area),
                                   fixed_area_um2=float(fixed_part.area),

                                   # seam=[None if anchor is None else [float(anchor[0]),float(anchor[1])],
                                   #       None if moved_anchor is None else [float(moved_anchor[0]),float(moved_anchor[1])]],
                                   # movable_area_um2=float(movable.area),moved_area_um2=float(moved.area),
                                   # fixed_area_um2=float(fixed_part.area),

                                   shift_um=shift_distance,cut_distance_um=block['distance'],

                                   chain_span_um=block['span'],
                                   gap_um=gap,bridge_length_um=length,bridge_area_um2=bridge_area,
                                   bridge_span_um=max(spans) if spans else None,
                                   taper_um=float(block.get('taper_um',0.)),
                                   tapered=bool(block.get('tapered')),
                                   moved_cut=(None if piece_cut is None
                                              else transform(piece_cut,center,states[owner])),
                                   extra_cut=extra_cut, extra_manual=bool(block.get('extra_plane')),
                                   extra_um_piece=(None if extra_cut is None or piece_cut is None else
                                                   round(float(shapely.distance(extra_cut,piece_cut)),6)),
                                   clearance_um=clearance,conflict=conflict,
                                   conflict_net=conflict_net,conflict_link=conflict_link,
                                   manual=bool(block.get('manual')),
                                   manual_lost=bool(block.get('manual_lost')),
                                   region=bool(block.get('region')),
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
        pieces.sort(key=lambda q: (q['layer'], -q['movable_area_um2'], q['index'], q['die']))
        # 边框刀自检：一块铜几刀、共几刀、哪几块多出第二刀
        seam_pieces = [q for q in pieces if q.get('frame_seam')]
        seam_counts = [len(shapely.get_parts(q['extra_cut'])) for q in seam_pieces
                       if q.get('extra_cut') is not None]
        seam_big = ['%s#%s×%d[%s]' % (q['layer'], q['index'],
                                      len(shapely.get_parts(q['extra_cut'])),
                                      ' '.join('(%.1f,%.1f)-(%.1f,%.1f)'
                                               % (g.coords[0][0], g.coords[0][1],
                                                  g.coords[-1][0], g.coords[-1][1])
                                               for g in shapely.get_parts(q['extra_cut'])[:4]))
                    for q in seam_pieces
                    if q.get('extra_cut') is not None
                    and len(shapely.get_parts(q['extra_cut'])) > 1]
        result = dict(complete=True, pieces=pieces, replacements=out, scope=SCOPE,
                    parameters=dict(length_um=self.length_um,width_um=self.width_um,overlap_um=self.overlap_um,
                                    landing_diameters=AUTO_LANDING_DIAMETERS),
                    stays=[[round(float(s['center'][0]), 3), round(float(s['center'][1]), 3)]
                           for s in self.stays],
                    summary=dict(pieces=len(pieces), chains=chain_total, stays=len(self.stays), stays_out=stays_out,
                                 fixed_ends=fixed_ends, fixed_covered=fixed_covered, fixed_points=len(fixed_points),
                                 fixed_sample=fixed_sample,
                                 fixed_regions=fixed_regions, fixed_first=fixed_first,
                                 bridges=bridges,orphans=orphans,flush=flush,conflicts=conflicts,
                                 groups_used=len({(q['layer'], q['index']) for q in pieces}),
                                 anchors_last=anchors_last,
                                 seam_pieces=len(seam_pieces),
                                 seam_seconds=sum(seam_counts),
                                 seam_most=max(seam_counts or [0]),
                                 seam_big=seam_big[:8],
                                 seam_big_n=len(seam_big),
                                 seam_cuts_n=len(seam_cuts),
                                 seam_cuts=' | '.join(seam_cuts[:4]),
                                 seam_debug_n=len(self._seam_debug),
                                 seam_debug=' | '.join(self._seam_debug[:3]),
                                 manual_cuts=len(self.cuts),manual_lost=lost,tapered=tapered_count,
                                 smooth_window_um=self.smooth_window_um,
                                 smooth_delta_um=self.smooth_delta_um,
                                 manual_regions=len(self.regions),
                                 regions_used=len(self.regions)-regions_unused,
                                 regions_unused=regions_unused,
                                 rule_space_um=self.rule_space,
                                 movable_area_um2=moved_area,cover_area_um2=cover_area,
                                 fixed_area_um2=fixed_area,max_shift_um=max_shift,
                                 width_um=self.width_um,length_um=self.length_um,
                                 bridge_extra_um=self.extra_um,dropped_cuts=len(self.dropped),
                                 landing_diameters=AUTO_LANDING_DIAMETERS))
        if not np.any(states) and not fingerprint:self.cache.pin(key)
        self.cache[key]=result;progress(100,'重构预演完成')
        return result
