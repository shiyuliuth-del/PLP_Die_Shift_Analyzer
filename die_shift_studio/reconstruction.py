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

# Distance from the farthest landing of a chain to the cut when no explicit
# distance is set.
AUTO_LANDING_DIAMETERS=2.5

SCOPE=('重构预演：软件自动把“运动过孔下方的固定RDL铜”划为可移动区——切分线垂直于该过孔下方那段铜的走向'
       '（与走线两侧都成90度）；同一块固定RDL上属于同一DIE、彼此相距不远的多个落盘视为一条串，'
       '切口不会落在两个相邻落盘之间，而是从这条串里最远的那一端再向外'
       f'{AUTO_LANDING_DIAMETERS:g}个落盘直径（可在设置中改成具体距离），网络那一侧保持不动，'
       '含落盘的那一段为移动区；'
       '移动区按所属DIE的刚体变换移动到过孔新位置，'
       '两个切口面的4个端点围成的多边形作为补线盖住裂隙——不论裂隙大小（是否短路）都会补，'
       '只有两个切口面完全重合、或缝隙已被铜盖住时才不重复补。'
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
    def __init__(self,model,parameters,settings,cache_group=None):
        self.model=model;self.p=parameters;self.settings=settings;self.cache=SessionCache(group=cache_group)
        self.tol=max(1e-9,model.data.dbu_um)
        self.length_um=max(0.,float(getattr(settings,'reconstruction_length_um',0.)))
        # Outward expansion of the cover polygon: 0 = exactly the four cut-face
        # corners, which is the default reconstruction.
        self.width_um=max(0.,float(getattr(settings,'reconstruction_width_um',0.)))
        self.overlap_um=max(0.,float(getattr(settings,'reconstruction_overlap_um',0.)))
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

    @staticmethod
    def _diameter(geometry):
        return 2.*float(np.sqrt(max(geometry.area,0.)/np.pi))

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

    def _block(self,polygon,cluster,states,owner,others=()):
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
        axis=self._chain_axis(polygon,cluster)
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
        size=2.*span+shift+3.*diameter+1.
        tangent=np.array([-axis[1],axis[0]])
        towards=float(np.dot(np.asarray(polygon.centroid.coords[0],float)-pivot,axis))
        copper=shapely.get_coordinates(polygon)
        center=self.model.bound.centers[owner]
        probe=max(self.tol*4.,.01)
        ends=[]
        for sign in (1.,-1.):
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
                behind=shapely.intersection(polygon,half_plane(origin,normal,size))
                if behind.is_empty or behind.area<=self.tol**2:return None
                line=shapely.LineString([origin-tangent*size,origin+tangent*size])
                # The cut face is where the plane really crosses copper. It cannot
                # be taken from `behind`: that polygon's own boundary IS this plane,
                # and intersecting a line with the edge it lies on keeps only float
                # slivers (a 25 μm trace came back as a 3 μm stub). So cross the
                # line with the untouched copper and keep the parts that have
                # copper on both sides of the plane.
                faces=[]
                for piece in line_parts(shapely.intersection(line,polygon)):
                    if piece.length<=self.tol:continue
                    mid=np.asarray(piece.interpolate(.5,normalized=True).coords[0],float)
                    if (shapely.contains(polygon,shapely.Point(mid-normal*probe))
                            and shapely.contains(polygon,shapely.Point(mid+normal*probe))):
                        faces.append(piece)
                if not faces:return None
                nearest=min(float(shapely.distance(shapely.Point(pivot),q)) for q in faces)
                return dict(movable=behind,faces=faces,distance=distance,span=extent,
                            nearest=nearest,normal=normal)
            # The stated distance is the cut: not searched, applied to both ends
            # of the chain, and the end whose seam is nearer the landings wins.
            best=probe_at(default) if low<default<high else None
            if best is not None:ends.append(best)
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
            return dict(movable=movable,faces=[],distance=default,span=extent,quality=None)
        ends.sort(key=lambda q:q['nearest'])
        best=ends[0]
        if len(ends)>1 and abs(ends[0]['nearest']-ends[1]['nearest'])<=self.tol:
            # Both ends cross the copper equally well: the network is the side
            # the feature mass lies on.
            best=ends[0] if np.dot(best['normal'],axis)*towards>=0 else ends[1]
        return dict(movable=best['movable'],faces=best['faces'],distance=best['distance'],
                    span=best['span'])

    def frame(self,states,deformation=None,progress=lambda *_:None):
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
        for order,((owner,layer,index),vias) in enumerate(sorted(groups.items())):
            polygon=self._copper(layer,index,replacements)
            if polygon is None or polygon.is_empty:continue
            threshold=self._step(max(v['land_diameter'] for v in vias))
            frame=self._own_frame(owner)
            chains=self._clusters(threshold,vias)
            blocks=[]
            for cluster in chains:
                # The cut may search outward only as far as the next landing of
                # the same net: cutting past it would put that landing on the far
                # side of a seam it does not belong to.
                others=[v['land'] for other in chains if other is not cluster for v in other]
                block=self._block(polygon,cluster,states,owner,others)
                if block is None:continue
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
            feature=features.setdefault((layer,index),dict(original=polygon,blocks=[]))
            feature['blocks'].extend(blocks)
            if order%64==0:progress(round(60*order/max(1,len(groups))),f'重构预演：划分移动区 {order}/{len(groups)}')
        pieces=[];out={}
        bridges=0;orphans=0;moved_area=0.;cover_area=0.;max_shift=0.;flush=0
        for (layer,index),feature in sorted(features.items()):
            original=feature['original'];blocks=feature['blocks']
            net=int(self.model.nodes[self.model.ids[layer,index]]['net'])
            stay={}
            for owner in sorted({q['owner'] for q in blocks}):
                own=[q['movable'] for q in blocks if q['owner']==owner]
                # Copper another die will move away is not a stationary obstacle
                # for this one: both blocks travel with their own die, so only
                # copper nobody moves can be shorted against.
                elsewhere=[q['movable'] for q in blocks if q['owner']!=owner]
                stay[owner]=shapely.difference(original,shapely.union_all(own+elsewhere))
            feature_moved=[];feature_covers=[]
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
                feature_moved.append(moved)
                shift_distance=float(np.linalg.norm(
                    np.asarray(moved.centroid.coords[0])-np.asarray(movable.centroid.coords[0])))
                max_shift=max(max_shift,shift_distance);moved_area+=float(movable.area)
                pieces.append(dict(die=owner,layer=layer,index=index,net=net,movable=movable,moved=moved,
                                   immovable=stationary,bridge=bridge,cut=shapely.union_all(faces),
                                   vias=[int(v['index']) for v in block['cluster']],
                                   seam=[None if anchor is None else [float(anchor[0]),float(anchor[1])],
                                         None if moved_anchor is None else [float(moved_anchor[0]),float(moved_anchor[1])]],
                                   movable_area_um2=float(movable.area),moved_area_um2=float(moved.area),
                                   shift_um=shift_distance,cut_distance_um=block['distance'],
                                   chain_span_um=block['span'],
                                   gap_um=gap,bridge_length_um=length,bridge_area_um2=bridge_area,
                                   orphan=stationary.is_empty))
            geometry=shapely.union_all([shapely.difference(original,
                                                          shapely.union_all([q['movable'] for q in blocks])),
                                        *feature_moved,*feature_covers])
            out.setdefault(layer,[]).append(dict(indices=(index,),geometry=geometry,net=net,
                                                 movable=shapely.union_all([q['movable'] for q in blocks]),
                                                 moved=shapely.union_all(feature_moved)))
        pieces.sort(key=lambda q:(q['layer'],-q['movable_area_um2'],q['index'],q['die']))
        result=dict(complete=True,pieces=pieces,replacements=out,scope=SCOPE,
                    parameters=dict(length_um=self.length_um,width_um=self.width_um,overlap_um=self.overlap_um,
                                    landing_diameters=AUTO_LANDING_DIAMETERS),
                    summary=dict(pieces=len(pieces),bridges=bridges,orphans=orphans,flush=flush,
                                 movable_area_um2=moved_area,cover_area_um2=cover_area,max_shift_um=max_shift,
                                 width_um=self.width_um,length_um=self.length_um,
                                 landing_diameters=AUTO_LANDING_DIAMETERS))
        if not np.any(states) and not fingerprint:self.cache.pin(key)
        self.cache[key]=result;progress(100,'重构预演完成')
        return result
