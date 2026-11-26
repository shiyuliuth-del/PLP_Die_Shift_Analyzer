"""Nominal shared A/B/C regions and independently draggable source endpoints."""
from collections import Counter,defaultdict
from dataclasses import replace
import time
import math
import numpy as np
import shapely
from .moving_pair import boundary_pose,relative_pose
from .between_motion import LABELS,STATIONARY,lookup_mode
from matplotlib.collections import LineCollection,PatchCollection,PathCollection
from matplotlib.lines import Line2D
from matplotlib.markers import MarkerStyle
from matplotlib.patches import Rectangle
from matplotlib.path import Path
from matplotlib.transforms import IdentityTransform
from .reconstruction import normalize_via_refs,transform
from .qt_compat import Qt,QtCore,QtWidgets as W
from .reconstruction_events import zone_key,active_region,color_for,PALETTE,RANK
from .edit_history import record

CUT_COLOR='#237ab9'
SECOND_CUT_COLOR=CUT_COLOR
WARNING_COLOR='#b570b8'
FOLLOW_COLOR='#c9b3eb'
FOLLOW_ALPHA=.45
LOW_PALETTE={'red':'#deb8bc','yellow':'#e3d5b2','green':'#c6d8cb','pending':'#dfe2e6'}
STAGED_STYLE={
    '忽略·随动':('#c9b3eb44','#7f4ec2'),
    '忽略·静止':('#f3d49344','#b87c24'),
    '取消忽略':('#b8d8c044','#2f8f5b'),
    '删除动区':('#ed9fa444','#c44a3d'),
    '删除全部动区':('#ed9fa444','#c44a3d'),
    '固定过孔随动':('#c9b3eb55','#7f4ec2'),
    '固定过孔静止':('#d9dde255','#6c7886'),
    '动区间随动':('#a6d4c555','#2a8568'),
}


def _lines(g):
    if g is None or g.is_empty:return []
    if g.geom_type=='Point':return [np.repeat(shapely.get_coordinates(g),2,axis=0)]
    if g.geom_type=='LineString':return [np.asarray(g.coords)]
    return [xy for q in getattr(g,'geoms',()) for xy in _lines(q)]


def _in_view(bounds,view):
    return bounds[0]<=view[2] and bounds[2]>=view[0] and bounds[1]<=view[3] and bounds[3]>=view[1]


def _segment_distance(points,x,y):
    a=points[:-1];v=np.diff(points,axis=0);norm=np.sum(v*v,axis=1)
    t=np.clip(np.sum((np.array([x,y])-a)*v,axis=1)/np.maximum(norm,1e-20),0,1)
    return float(np.min(np.linalg.norm(a+t[:,None]*v-[x,y],axis=1)))


def display_material(data,visible_layers):
    """The canvas base uses the same copper as full DRC and preview export."""
    data=data or {}
    following={(q['layer'],q['index']) for q in data.get('pieces',()) if q.get('ignored_following')}
    copper={}
    for layer in visible_layers:
        for q in data.get('replacements',{}).get(layer,()):
            g=q['geometry']
            if not g.is_empty:copper[layer,q['indices'][0]]=g
    return copper,following


def between_color(rgb,mode):
    if mode==STATIONARY:return tuple(c*.69 for c in rgb)
    if mode=='min':return tuple(c*.87 for c in rgb)
    if mode=='max':return tuple(c+(1-c)*.25 for c in rgb)
    return rgb


class ReconstructionOverlay:
    def __init__(self,window):
        self.draft_frame=self.draft_data=self.draft_key=None;self.staged_overlay=None;self._stage_worker=None;self._stage_pending=None;self._stage_ref_keys={}
        self._stage_frame=None;self.between_views={}
        self.w=window;self.artists={};self.stationary_artists={};self.cuts=None;self.stamp=self.table_stamp=None
        self.selected=self.selected_kind=None;self.hover=None;self.hover_key=None
        self.end_artists=[];self.region_artists=[];self.arrow_artists=[];self.path_cache={};self.handles={};self.union_cache={};self.labels=[];self.staged_tags=[];self.text_sizes={};self.zone_views={};self.zone_low={};self.zone_clips={}
        self.pulse_timer=QtCore.QTimer(window);self.pulse_timer.setInterval(33);self.pulse_timer.timeout.connect(self.pulse)
    def active(self):return self.w.reconstruction_toggle.isChecked()
    @staticmethod
    def _key(q):return q['layer'],int(q['index']),normalize_via_refs(q.get('via_refs',q['vias'])),q.get('route_identity')
    def data(self):
        frame=self.w.current_frame
        if getattr(self,'draft_frame',None) is not None:
            if self.draft_frame is frame:return self.draft_data
            self.draft_frame=self.draft_data=None
        return (frame or {}).get('reconstruction')
    def stage(self,*,after_failed_drag=False):
        w=self.w;frame=w.current_frame
        cfg=w.view_settings;previous=w._applied_preview_settings
        if previous is None:
            # The first frame may still be running when the editor opens.
            # Preserve pending geometry until an applied baseline is available.
            from .settings import ViewSettings
            previous=ViewSettings()
        key=cfg.reconstruction_key()
        if not after_failed_drag and self._stage_frame is frame and self.staged_overlay and self.staged_overlay['key']==key:return
        self._stage_frame=frame
        if key==previous.reconstruction_key():
            self._cancel_stage_worker()
            self.draft_frame=self.draft_data=self.draft_key=None;self.staged_overlay=None;self.stamp=None;return
        solver=getattr(getattr(w,'engine',None),'reconstructor',None)
        geo=solver.model.cache.layers if solver is not None else (w.geometry_cache.layers if getattr(w,'geometry_cache',None) is not None else {})
        rdl_refs=set();via_refs=set();between_refs=set();staged_boxes=[];actions={}
        def tag(ref,label):
            items=actions.setdefault(ref,[])
            if label not in items:items.append(label)
        cur_ignored=set(cfg.reconstruction_ignored);prev_ignored=set(previous.reconstruction_ignored)
        cur_follow=set(cfg.reconstruction_following);prev_follow=set(previous.reconstruction_following)
        for ref in sorted(cur_ignored-prev_ignored):
            rdl_refs.add(ref);tag(ref,'忽略·随动' if ref in cur_follow else '忽略·静止')
        for ref in sorted(prev_ignored-cur_ignored):
            rdl_refs.add(ref);tag(ref,'取消忽略')
        for ref in sorted(cur_follow^prev_follow):
            rdl_refs.add(ref)
            if ref in cur_ignored:tag(ref,'忽略·随动' if ref in cur_follow else '忽略·静止')
        from .manual_regions import ordered_boxes
        old_boxes=Counter(ordered_boxes(previous))
        for row in ordered_boxes(cfg):
            if old_boxes[row]:
                old_boxes[row]-=1
            else:
                _,layer,vertices=row
                staged_boxes.append((layer,shapely.Polygon(vertices)))
        for name in ('ignored','following','forced','angles','intervals','added','deleted','regions','boxes','angle_boxes','box_starts','box_excluded','cuts','extra_cuts','second_cuts','dropped_cuts','fixed_via_follows','between_overrides'):
            field='reconstruction_'+name
            cur=set(getattr(cfg,field));prev=set(getattr(previous,field))
            for row in cur^prev:
                if name in ('boxes','angle_boxes','regions'):
                    g=shapely.Polygon(row[1]) if name!='regions' else shapely.box(*row[1]);layer=row[0]
                    if layer in geo:
                        for i in np.flatnonzero(shapely.intersects(geo[layer]['geometry'],g)):
                            ref=(layer,int(i));rdl_refs.add(ref);tag(ref,'框选动区')
                elif name=='fixed_via_follows':
                    ref=(str(row[0]),int(row[1]));via_refs.add(ref)
                    tag(ref,'固定过孔随动' if row in cur else '固定过孔静止')
                elif name=='between_overrides':
                    ref=(str(row[0]),int(row[1]));between_refs.add(ref);rdl_refs.add(ref)
                    tag(ref,'动区间随动')
                else:
                    ref=(str(row[0]),int(row[1]));rdl_refs.add(ref)
                    if name=='deleted' and row in cur:tag(ref,'删除动区')
                    elif name=='added' and row in cur:tag(ref,'新增动区')
                    elif name=='angles':tag(ref,'旋转边界')
                    elif name in ('intervals','cuts','extra_cuts','second_cuts'):tag(ref,'调整动区')
                    elif name=='box_excluded' and row in cur:tag(ref,'删除动区')
        all_refs=rdl_refs|via_refs
        self.staged_overlay=dict(refs=all_refs,between_refs=between_refs,boxes=tuple(staged_boxes),actions={k:tuple(v) for k,v in actions.items()},key=key,previous=previous)
        if frame and frame.get('reconstruction') and solver is not None and rdl_refs and not getattr(w,'_in_animation',False):
            data=frame['reconstruction']
            draft_refs=all_refs
            # Only recompute refs whose per-ref settings changed since the last draft
            # on this same frame, instead of rebuilding every previously staged shape.
            def ref_sig(target):
                return (target in cfg.reconstruction_ignored,target in cfg.reconstruction_following,target in cfg.reconstruction_forced,target in cfg.reconstruction_box_excluded,
                        tuple(r for r in cfg.reconstruction_intervals if (r[0],r[1])==target),
                        tuple(r for r in cfg.reconstruction_angles if (r[0],r[1])==target),
                        tuple(r for r in cfg.reconstruction_added if (r[0],r[1])==target),
                        tuple(r for r in cfg.reconstruction_deleted if (r[0],r[1])==target),
                        tuple(r for r in cfg.reconstruction_cuts if (r[0],r[1])==target),
                        tuple(r for r in cfg.reconstruction_extra_cuts if (r[0],r[1])==target),
                        tuple(r for r in cfg.reconstruction_second_cuts if (r[0],r[1])==target),
                        tuple(r for r in cfg.reconstruction_dropped_cuts if (r[0],r[1])==target),
                        tuple(r for r in cfg.reconstruction_between_overrides if (r[0],r[1])==target),
                        tuple(r for r in cfg.reconstruction_box_starts if (r[0],r[1])==target),
                        cfg.reconstruction_between_motion,
                        cfg.reconstruction_boxes,cfg.reconstruction_angle_boxes,cfg.reconstruction_box_order,cfg.reconstruction_regions,
                        cfg.reconstruction_style,cfg.reconstruction_boundary_mode,cfg.reconstruction_allow_moving_pad)
            reuse_draft=(self.draft_frame is frame and self.draft_data is not None and self._stage_ref_keys.keys()<=rdl_refs)
            base_frame=dict(frame,reconstruction=self.draft_data) if reuse_draft else frame
            prev_sigs=self._stage_ref_keys if reuse_draft else {}
            new_sigs={ref:ref_sig(ref) for ref in rdl_refs}
            dirty_refs=sorted(ref for ref in rdl_refs if prev_sigs.get(ref)!=new_sigs[ref])
            drag=w.cut_drag
            retained=drag.retained
            if retained is not None and retained['frame'] is frame and retained.get('live_key', (None,))[0]==key:
                edited=set(dirty_refs)==retained['refs']
                if edited and self.draft_frame is frame and self.draft_key==key:
                    self._stage_ref_keys=new_sigs
                    self.draft_data=dict(self.draft_data,draft_refs=draft_refs)
                    dirty_refs=[]
                    reuse_draft=True
                if edited and (drag.worker is not None or drag.pending is not None):
                    # Pointer release and its already running local preview
                    # share the same inputs. Paint tags now and let it publish
                    # the draft, without a second synchronous solver call.
                    self._cancel_stage_worker();self.stamp=None
                    return
            if not dirty_refs and reuse_draft:
                self.draft_key=key
                self.draft_data=dict(self.draft_data,draft_refs=draft_refs)
            else:
                piece_count=sum(1 for q in data['pieces'] if (q['layer'],q['index']) in dirty_refs)
                if (piece_count>8 or between_refs.intersection(dirty_refs) or drag.worker is not None or after_failed_drag) and not getattr(w,'_closing',False):
                    # Heavy multi-zone polygon: paint staged markers immediately on the main
                    # thread and compute the local draft asynchronously so the UI never hangs.
                    self._stage_pending=(frame,base_frame,solver,cfg,dirty_refs,new_sigs,draft_refs,key)
                    self._start_stage_worker()
                else:
                    self._cancel_stage_worker()
                    from .reconstruction_zones import live_serial
                    local=base_frame
                    for ref in dirty_refs:
                        try:local=live_serial(solver,cfg,local,ref,draft=True)
                        except (ValueError,shapely.errors.GEOSException) as exc:w.log_bus.emit(f'待算局部预览 {ref[0]} #{ref[1]+1}：{exc}')
                    self.draft_frame=frame;self.draft_key=key;self._stage_ref_keys=new_sigs
                    self.draft_data=dict(local['reconstruction'],draft_refs=draft_refs)
        elif frame and frame.get('reconstruction') and solver is not None:
            self._cancel_stage_worker()
            data=frame['reconstruction']
            self.draft_frame=frame;self.draft_key=key;self._stage_ref_keys={}
            general=('reconstruction_style','reconstruction_boundary_mode','reconstruction_die_mode')
            draft_refs={ (q['layer'],q['index']) for q in data['pieces']} if any(getattr(cfg,name)!=getattr(previous,name) for name in general) else all_refs
            self.draft_data=dict(data,draft_refs=draft_refs)
        self.stamp=None

    def _cancel_stage_worker(self):
        self._stage_pending=None
        if self._stage_worker is not None:
            self._stage_worker.cancelled.set()

    def _start_stage_worker(self):
        if self._stage_worker is not None:
            self._stage_worker.cancelled.set()
            return
        if self._stage_pending is None or getattr(self.w,'_closing',False):return
        from .gui import Worker
        from .reconstruction_zones import live_serial
        frame,base_frame,solver,cfg,dirty_refs,new_sigs,draft_refs,key=self._stage_pending
        self._stage_pending=None
        def work(_):
            local=base_frame
            for ref in dirty_refs:
                local=live_serial(solver,cfg,local,ref,draft=True)
            return local
        worker=Worker(work,self.w)
        self._stage_worker=worker
        def accepted(local):
            if worker.cancelled.is_set() or getattr(self.w,'_closing',False):return
            if self.w.current_frame is not frame or not getattr(self.w,'reconstruction_pending',False):return
            if self.w.view_settings.reconstruction_key()!=key:return
            self.draft_frame=frame;self.draft_key=key;self._stage_ref_keys=new_sigs
            self.draft_data=dict(local['reconstruction'],draft_refs=draft_refs)
            try:
                self.stamp=None;self.draw();self.w.selection.draw(redraw=False);self.w.render_scene()
            except Exception:
                from traceback import format_exc
                rejected(format_exc());return
            self.w.finish_reconstruction_edit(key,local)
        def finished():
            if self._stage_worker is worker:self._stage_worker=None
            worker.deleteLater()
            if getattr(self.w,'_closing',False):
                QtCore.QTimer.singleShot(0,self.w.close)
            elif self._stage_pending is not None:
                self._start_stage_worker()
        worker.done.connect(accepted)
        def rejected(message):
            if worker.cancelled.is_set() or self.w.current_frame is not frame or self.w.view_settings.reconstruction_key()!=key:return
            if not self.w.rollback_reconstruction_edit(message,key):
                self.w.log_bus.emit('待计算动区预览未生成：\n'+message,'ERROR')
        worker.failed.connect(rejected)
        worker.finished.connect(finished)
        worker.start()

    def pieces(self):return [q for q in (self.data() or {}).get('pieces',()) if self.active() and self.w.visible_boxes[q['layer']].isChecked()]
    def piece_for(self,key):return next((q for q in self.pieces() if self._key(q)==key),None)

    def linked_regions(self,piece):
        """Return the visible regions connected by the same box selection."""
        unlinked=set(self.w.view_settings.reconstruction_unlinked_boxes)
        groups=set(piece.get('box_groups',()))-unlinked
        regions=[piece]
        if not groups:return regions,groups
        pending=[q for q in self.pieces() if q is not piece and q.get('box_groups') and not q.get('box_invalid')]
        while pending:
            matched=[q for q in pending if groups.intersection(q['box_groups'])]
            if not matched:break
            regions.extend(matched)
            groups.update(g for q in matched for g in q['box_groups'] if g not in unlinked)
            used={id(q) for q in matched}
            pending=[q for q in pending if id(q) not in used]
        return regions,groups
    def handle_geometries(self,pieces):
        frame=self.w.current_frame or {};result={};groups={}
        for q in pieces:
            plan=q.get('route_plan')
            for kind,side,name in (('moved',0,'moved_cut'),('extra',1,'extra_cut')):
                g=q.get(name);key=(self._key(q),kind)
                if getattr(self.w,'reconstruction_pending',False) and not self.w.view_settings.reconstruction_auto and getattr(self,'draft_frame',None) is not frame:
                    staged=[];changed=False
                    for member in q.get('graph_members',(q,)):
                        route=member.get('route_plan')
                        if route is None:continue
                        saved=next((row for row in self.w.view_settings.reconstruction_intervals if (row[0],row[1],row[2],normalize_via_refs(row[3]),row[4])==(*route.key,route.identity)),None)
                        part=route.partition(*saved[-2:]) if saved is not None else None
                        if part is None:
                            if member.get(name) is not None:staged.append(member[name])
                            continue
                        from .reconstruction_edits import apply_angles
                        part=apply_angles(self.w.view_settings,route,part) or part
                        staged.append(transform(part['far' if side else 'near'],*boundary_pose(q,frame,side)));changed=True
                    if changed:g=shapely.union_all(staged)
                if g is None:continue
                if q.get('graph_members'):
                    # The merged zone owns this blade/dot. Its first member may
                    # start at an unrelated port on the other end of a plane.
                    result[key]=g
                elif plan is not None and g.geom_type=='Point':
                    local=plan.pad_handle(side)
                    local=transform(local,*boundary_pose(q,frame,side))
                    result[key]=local
                    anchor=tuple(np.round(np.asarray(plan.route.coords[0 if side==0 else -1]),7))
                    groups.setdefault((q['layer'],kind,q['die'] if side==0 else -1,anchor),[]).append((key,g,local))
                else:result[key]=g
        # Same-pad branch handles must remain individually pickable at any zoom.
        for rows in groups.values():
            if len(rows)<2:continue
            center=np.asarray(self.w.ax.transData.transform(rows[0][1].coords[0]))
            angles=[]
            for key,actual,display in rows:
                d=self.w.ax.transData.transform(display.coords[0])-center
                angles.append(float(np.arctan2(d[1],d[0])))
            order=np.argsort(angles);angles=np.unwrap(np.array(angles)[order])
            for i in range(1,len(angles)):
                if angles[i]-angles[i-1]<min(1.,2*np.pi/max(3,len(rows))):angles[i]=angles[i-1]+min(1.,2*np.pi/max(3,len(rows)))
            radius=max(20.,9*len(rows))
            placed=[]
            for j,angle in zip(order,angles):
                direction=np.array([np.cos(angle),np.sin(angle)]);distance=radius
                point=center+distance*direction
                # Angular spreading can wrap for many branches; retain a strict
                # screen-space pick distance even in that degenerate case.
                while any(np.linalg.norm(point-other)<23. for other in placed):
                    distance+=24.;point=center+distance*direction
                placed.append(point)
                result[rows[j][0]]=shapely.Point(self.w.ax.transData.inverted().transform(point))
        return result

    def dragged_handle(self):
        drag=getattr(self.w,'cut_drag',None)
        if drag is None:return None
        edit=drag.state or drag.retained
        if edit is None or edit.get('marker') is None:return None
        frame=self.w.current_frame
        if drag.state is None and self.draft_frame is frame and self.draft_data is not None:
            if self.draft_key==self.w.view_settings.reconstruction_key():
                # The solved merge owns its new outer blades. A retained
                # pointer marker must not hide them, even in manual mode.
                drag.retained=None
                return None
        if frame is edit['frame'] or frame is edit.get('live_frame'):return edit
        drag.retained=None
        return None

    @staticmethod
    def original_handles(q,kind,edit):
        if edit is None:return ()
        identities={getattr(member.get('route_plan'),'identity',None) for member in q.get('graph_members',(q,))}
        return tuple(row for row in edit['edited_handles'] if row['kind']==kind and row['ref']==(q['layer'],q['index'])
                     and not identities.isdisjoint(row['identities']))

    @staticmethod
    def owns_handle(q,kind,edit):
        return bool(ReconstructionOverlay.original_handles(q,kind,edit))

    def risk_bank(self):return getattr(getattr(self.w,'reconstruction_yield',None),'evidence',None)

    def draw_stationary_pattern(self,clips,viewport):
        ax=self.w.ax
        if clips:
            width,height,dpi=viewport
            pitch=14*dpi/72
            xs,ys=np.meshgrid(np.arange(pitch/4,width+pitch,pitch),np.arange(pitch/2,height,pitch/2))
            xs[1::2]+=pitch/2
            offsets=np.column_stack((xs.ravel()/width,ys.ravel()/height))
        for layer,paths in clips.items():
            artist=self.stationary_artists.get(layer)
            if artist is None:
                diamond=MarkerStyle('D')
                marker=diamond.get_path().transformed(diamond.get_transform())
                artist=PathCollection([marker],sizes=[5.],offset_transform=ax.transAxes,
                                      facecolors='#696d73',edgecolors='none',alpha=.68)
                artist.set_transform(IdentityTransform())
                ax.add_collection(artist,autolim=False)
                artist.set_clip_box(ax.bbox)
                self.stationary_artists[layer]=artist
            artist.set_offsets(offsets)
            artist.set_clip_path(Path.make_compound_path(*paths),ax.transData)
            artist.set_zorder(self.artists[layer,'C'].get_zorder()+.0005)
            artist.set_visible(True)
        for layer,artist in self.stationary_artists.items():
            if layer not in clips:artist.set_visible(False)

    def draw(self):
        w=self.w
        if self.cuts is None or self.cuts.axes is not w.ax:
            self.artists={};self.stationary_artists={}
            self.staged=PatchCollection([],facecolor='#5992b62a',edgecolor='#44698c',linestyles='--',linewidths=1.3,zorder=10);w.ax.add_collection(self.staged)
            self.warning=PatchCollection([],facecolor='none',edgecolor=WARNING_COLOR,linestyles='--',linewidths=.8,zorder=9.4);w.ax.add_collection(self.warning)
            self.problems=LineCollection([],colors='#b92d39',alpha=.7,linestyles='--',linewidths=.9,zorder=9.7);w.ax.add_collection(self.problems)
            self.low_problems=LineCollection([],colors='#b92d39',alpha=.28,linestyles=':',linewidths=.7,zorder=9.6);w.ax.add_collection(self.low_problems)
            self.cuts=LineCollection([],colors=CUT_COLOR,linewidths=2.2,zorder=11.5);w.ax.add_collection(self.cuts)
            self.second_cuts=LineCollection([],colors=SECOND_CUT_COLOR,linewidths=2.2,zorder=11.6);w.ax.add_collection(self.second_cuts)
            self.points=Line2D([],[],ls='none',marker='o',markersize=8,color=CUT_COLOR,markeredgecolor='white',markeredgewidth=.8,zorder=12);w.ax.add_line(self.points)
            self.second_points=Line2D([],[],ls='none',marker='o',markersize=8,color=SECOND_CUT_COLOR,markeredgecolor='white',markeredgewidth=.8,zorder=12);w.ax.add_line(self.second_points)
            self.stamp=None
        if self.active() and getattr(w,'reconstruction_pending',False) and self._stage_frame is not w.current_frame:
            self.stage()
        data=self.data() if self.active() else None;bank=self.risk_bank() if self.active() else None
        vis=tuple(k for k,v in w.visible_boxes.items() if v.isChecked());order=tuple(w.ordered_layers())
        nominal=bool((w.current_frame or {}).get('nominal_global'))
        pose_low,pose_prob,threshold=w.is_low_probability_pose() if hasattr(w,'is_low_probability_pose') else (False,None,None)
        edit=self.dragged_handle() if data else None
        # During the gesture the moving handle is a single animated artist.
        # After release it is painted once in the static collection until the
        # committed frame takes ownership.
        dragging=edit is not None and w.cut_drag.state is edit
        edit_stamp=(id(edit),dragging,0 if dragging else id(edit['marker'])) if edit else None
        staged_info=self.staged_overlay if (self.active() and getattr(w,'reconstruction_pending',False)) else None
        allow_pad=w.view_settings.reconstruction_allow_moving_pad
        deleted=getattr(w,'pending_deleted_shapes',None) if self.active() and getattr(w,'reconstruction_pending',False) else None
        restored=getattr(w,'pending_restored_shapes',None) if self.active() and getattr(w,'reconstruction_pending',False) else None
        nodes=getattr(w,'pending_node_shapes',None) if self.active() and getattr(w,'reconstruction_pending',False) else None
        cleared=getattr(w,'_drc_cleared_figures',())
        catalogue_waivers=w.drc_waiver_summary()[2] if nominal and hasattr(w,'drc_waiver_summary') else {}
        waiver_stamp=(id(bank),cleared,tuple(sorted(catalogue_waivers.items())))
        if getattr(self,'waiver_stamp',None)!=waiver_stamp:
            self.waiver_stamp=waiver_stamp;self._nominal_waiver_cache={}
        viewport=(max(1.,w.ax.bbox.width),max(1.,w.ax.bbox.height),w.ax.figure.dpi)
        stamp=(id(data),id(bank),vis,w.view_bounds,order,nominal,pose_low,threshold,edit_stamp,repr(w.layer_styles),id(staged_info),allow_pad,tuple(sorted(deleted)) if deleted else (),tuple(sorted(restored)) if restored else (),id(nodes),waiver_stamp,viewport)
        if stamp==self.stamp:self.draw_labels();return
        if self.stamp is not None and self.stamp[0]!=id(data) and edit is None:
            if self.hover is not None:self.hover.set_visible(False)
            self.pulse_timer.stop();self.hover_key=None
        w.scene_renderer.feedback_background=None
        self.stamp=stamp;paths={};colors={};first=[];second=[];dots=[];far_dots=[];warnings=[];region_groups={};red=[];low_red=[]
        self.between_views={};active_by_ref=defaultdict(list)
        old_unions=self.union_cache;self.union_cache={};old_cache=self.path_cache;self.path_cache={};self.zone_views={};self.zone_low={}
        old_clips=self.zone_clips;self.zone_clips={}
        def patch(g):
            previous=old_cache.get(id(g));item=previous if previous is not None and previous[0] is g else (g,w.geometry_patch(g))
            self.path_cache[id(g)]=item;return item[1]
        if not self.active():self.clear_hover()
        current=self.pieces();self.handles=self.handle_geometries(current)
        self.hit_items=[]
        # Draw the exact final replacement once. Independently filling A/B/C
        # creates seams and omits copper added by continuity compensation.
        copper,following=display_material(data,vis)
        verified=bool((data or {}).get('constraints_complete'))
        unchecked=set((data or {}).get('draft_refs',()))
        if bank and bank.get('configuration')!= (data or {}).get('configuration'):bank=None
        for q in current:
            ref=(q['layer'],q['index'])
            edited=bool(edit and ref in edit['refs'])
            status=color_for(q,bank,nominal,checked=verified or bool(unchecked and ref not in unchecked))
            waived=False
            if status=='yellow':
                if nominal and bank and ref in catalogue_waivers:
                    from .extreme_filter import ProblemIndex
                    zk=zone_key(q)
                    if zk not in self._nominal_waiver_cache:
                        risk=bank['zones'][zk]
                        index=ProblemIndex(bank['grid'],risk['clauses'],catalogue_waivers[ref])
                        self._nominal_waiver_cache[zk]=not (index.total or index.zero)
                    waived=self._nominal_waiver_cache[zk]
                elif not nominal and ref in cleared:waived=True
                if waived:status='green'
            if ref in unchecked or (edited and (dragging or not verified)):status='pending'
            is_low=False
            if status in ('red','yellow'):
                if not nominal and pose_low:is_low=True
                elif bank and threshold:
                    prob=(bank.get('zones',{}).get(zone_key(q)) or {}).get('probability')
                    if prob and prob.get('certified') and prob.get('upper',1.)<=threshold:is_low=True
            gactive=active_region(q)
            material=copper.get(ref)
            if material is not None and not gactive.is_empty and not q.get('connection_failed'):
                key=(id(material),id(gactive))
                clip=old_clips.get(key)
                if clip is None:clip=(material,gactive,gactive if material.covers(gactive) else material.intersection(gactive))
                self.zone_clips[key]=clip;gactive=clip[2]
            zk=zone_key(q)
            self.zone_views[zk]=(q,status,gactive);self.zone_low[zk]=is_low
            paint_status=('low_'+status) if is_low else status
            if not q.get('connection_failed') and not gactive.is_empty:active_by_ref[ref].append(gactive)
            if not gactive.is_empty and _in_view(gactive.bounds,w.view_bounds):
                region_groups.setdefault((*ref,'A',paint_status),{})[id(gactive)]=gactive
                if status=='red':
                    outline=q.get('diagnostic_outline') if not nominal else None
                    (low_red if is_low else red).extend(_lines(gactive.boundary if outline is None else outline))
                if q.get('routing_recovered') and not waived:warnings.append(patch(gactive))
            if q.get('box_invalid'):continue
            for kind,target,dot_list,line_list in (('moved','moved_cut',dots,first),('extra','extra_cut',far_dots,second)):
                face=self.handles.get((self._key(q),kind),q.get(target))
                if face is not None and not allow_pad:
                    if face.geom_type in ('Point','MultiPoint'):continue
                    if face.geom_type=='GeometryCollection':
                        face=shapely.GeometryCollection([part for part in face.geoms if part.geom_type not in ('Point','MultiPoint')])
                handles=self.original_handles(q,kind,edit)
                if handles:
                    if face is None:continue
                    components=list(shapely.get_parts(face))
                    markers=[row.get('painted_marker',row['original_marker']) if ref in unchecked else row['original_marker'] for row in handles]
                    removed={min(range(len(components)),key=lambda i:components[i].distance(marker))
                             for marker in markers if components}
                    components=[face for i,face in enumerate(components) if i not in removed]
                    face=shapely.GeometryCollection(components)
                if face is not None and not face.is_empty:
                    if q.get('route_plan') is not None:self.hit_items.append((q,kind,face))
                    for component in shapely.get_parts(face):
                        if component.geom_type in ('Point','MultiPoint'):dot_list.extend(shapely.get_coordinates(component))
                        else:line_list.extend(_lines(component))
        if edit is not None and not dragging:
            for handle in edit['edited_handles']:
                face=handle.get('marker');kind=handle['kind']
                if face is None:continue
                self.hit_items.append((handle['piece'],kind,face))
                if face.geom_type=='Point':(far_dots if kind=='extra' else dots).append(face.coords[0])
                else:(second if kind=='extra' else first).extend(_lines(face))
        self.hit_geometry=shapely.STRtree([g for _,_,g in self.hit_items])
        paint_by_ref=defaultdict(list);staged_between=[]
        previous=(staged_info or {}).get('previous')
        for block in (data or {}).get('between_blocks',()):
            ref=block['ref']
            base=copper.get(ref);g=block['geometry']
            if base is None or g.is_empty or not _in_view(g.bounds,w.view_bounds):continue
            if not base.covers(g):g=base.intersection(g)
            active=active_by_ref.get(ref,())
            if active:g=g.difference(shapely.union_all(active))
            if g.area<=(data or {}).get('dbu_um',.001)**2:continue
            self.between_views[block['id']]=dict(block,geometry=g)
            if block['mode']!='average':
                paint_by_ref[ref].append(g)
                region_groups.setdefault((*ref,'C',block['mode']),{})[id(g)]=g
            if previous is not None and not block.get('locked'):
                before=lookup_mode(previous,ref,block['nominal'])
                after=lookup_mode(w.view_settings,ref,block['nominal'])
                if before!=after:
                    staged_between.append((ref,g,LABELS[after[0]]))
        for (layer,index),g in copper.items():
            if not _in_view(g.bounds,w.view_bounds):continue
            middle=paint_by_ref.get((layer,index))
            if middle:g=g.difference(shapely.union_all(middle))
            if not g.is_empty:
                region_groups[layer,index,'B','following' if (layer,index) in following else '']={id(g):g}
        stationary_clips=defaultdict(list)
        for (layer,index,kind,status),values in sorted(region_groups.items(),key=lambda item:RANK.get(item[0][3].removeprefix('low_'),-1)):
            geos=tuple(values.values());cache_key=(layer,index,kind,status,tuple(values));previous=old_unions.get(cache_key)
            g=previous[1] if previous is not None and all(a is b for a,b in zip(previous[0],geos)) else (geos[0] if len(geos)==1 else shapely.union_all(geos))
            from matplotlib.colors import to_rgba
            if kind=='A':
                low=status.startswith('low_');base=status.removeprefix('low_')
                color=LOW_PALETTE[base] if low else PALETTE[base]
                alpha=.24 if low else .66
            else:
                color=FOLLOW_COLOR if status=='following' else w.style_for(layer)['color']
                alpha=FOLLOW_ALPHA if status=='following' else w.style_for(layer)['alpha']
                if kind=='C':color=between_color(to_rgba(color)[:3],status)
            shape=patch(g)
            if kind=='C' and status==STATIONARY:stationary_clips[layer].append(shape.get_path())
            self.union_cache[cache_key]=(geos,g);paths.setdefault((layer,kind),[]).append(shape);colors.setdefault((layer,kind),[]).append(to_rgba(color,alpha))
        for key in paths:
            if key not in self.artists:
                artist=PatchCollection([],linewidths=0);w.ax.add_collection(artist);self.artists[key]=artist
        for (layer,k),artist in self.artists.items():
            shapes=paths.get((layer,k),[]);artist.set_paths(shapes);artist.set_visible(bool(shapes));artist.set_facecolors(colors.get((layer,k),[]));artist.set_edgecolors('none')
            artist.set_alpha(None)
            artist.set_zorder(2+(order.index(layer) if layer in order else 0)/max(1,len(order))+.001*('BCA'.index(k)+1))
        self.draw_stationary_pattern(stationary_clips,viewport)
        from matplotlib.colors import to_rgba
        staged=[];staged_faces=[];staged_edges=[];tag_items=[]
        staged_refs=set((data or {}).get('draft_refs',()))
        staged_actions=(staged_info or {}).get('actions',{})
        if staged_info:staged_refs.update(staged_info.get('refs',()))
        placed_between=set()
        for ref,g,mode_label in staged_between:
            face,edge=STAGED_STYLE['动区间随动'];placed_between.add(ref)
            staged.append(patch(g));staged_faces.append(to_rgba(face));staged_edges.append(to_rgba(edge))
            if len(tag_items)<12:
                anchor=g.representative_point()
                tag_items.append((anchor.x,anchor.y,'待算：'+mode_label,edge))
        solver=getattr(getattr(w,'engine',None),'reconstructor',None)
        geo_layers=solver.model.cache.layers if solver is not None else (w.geometry_cache.layers if getattr(w,'geometry_cache',None) is not None else {})
        marked_refs=staged_refs|((staged_info or {}).get('between_refs',set())-placed_between)
        for layer,index in sorted(marked_refs):
            if layer in vis:
                ref=(layer,index);ops=tuple(op for op in staged_actions.get(ref,()) if op!='动区间随动' or ref not in placed_between)
                if ref in placed_between and not ops:continue
                face,edge=STAGED_STYLE.get(ops[-1],('#5992b633','#2b6ea3')) if ops else ('#5992b62a','#44698c')
                gs=[g for (l,i,kind,status),parts in region_groups.items() if (l,i)==ref and kind=='A' for g in parts.values()]
                if not gs and layer in geo_layers and index<len(geo_layers[layer]['geometry']):
                    gs=[geo_layers[layer]['geometry'][index]]
                visible_gs=[g for g in gs if not g.is_empty and _in_view(g.bounds,w.view_bounds)]
                for g in visible_gs:
                    staged.append(patch(g));staged_faces.append(to_rgba(face));staged_edges.append(to_rgba(edge))
                if visible_gs and ops and len(tag_items)<12:
                    anchor=visible_gs[0].representative_point()
                    tag_items.append((anchor.x,anchor.y,'待算：'+' + '.join(ops),edge))
        if staged_info:
            for layer,box_poly in staged_info.get('boxes',()):
                if layer in vis and not box_poly.is_empty and _in_view(box_poly.bounds,w.view_bounds):
                    staged.append(patch(box_poly));staged_faces.append(to_rgba('#5992b628'));staged_edges.append(to_rgba('#2b6ea3'))
        for (layer,_),g in (deleted or {}).items():
            if layer not in vis or g.is_empty or not _in_view(g.bounds,w.view_bounds):continue
            face,edge='#d3848138','#a14747'
            staged.append(patch(g));staged_faces.append(to_rgba(face));staged_edges.append(to_rgba(edge))
            if len(tag_items)<12:
                point=g.representative_point();tag_items.append((point.x,point.y,'待算：删除图形',edge))
        for (layer,_),g in (restored or {}).items():
            if layer not in vis or g.is_empty or not _in_view(g.bounds,w.view_bounds):continue
            face,edge='#5eb38238','#277a4d'
            staged.append(patch(g));staged_faces.append(to_rgba(face));staged_edges.append(to_rgba(edge))
            if len(tag_items)<12:
                point=g.representative_point();tag_items.append((point.x,point.y,'待算：撤销删除',edge))
        for (layer,_),g in (nodes or {}).items():
            if layer not in vis or g.is_empty or not _in_view(g.bounds,w.view_bounds):continue
            face,edge='#5992b638','#2b6ea3'
            staged.append(patch(g));staged_faces.append(to_rgba(face));staged_edges.append(to_rgba(edge))
            if len(tag_items)<12:
                point=g.representative_point();tag_items.append((point.x,point.y,'待算：节点修改',edge))
        self.staged.set_paths(staged);self.staged.set_visible(bool(staged))
        if staged:
            self.staged.set_facecolors(staged_faces);self.staged.set_edgecolors(staged_edges);self.staged.set_alpha(None)
        for i,(tx,ty,label_text,edge_color) in enumerate(tag_items):
            if i==len(self.staged_tags) or self.staged_tags[i].axes is not w.ax:
                t=w.ax.text(tx,ty,label_text,fontsize=7.5,ha='center',va='center',zorder=16,clip_on=True,
                            bbox=dict(boxstyle='round,pad=0.18',facecolor='white',edgecolor=edge_color,alpha=.90,lw=.9))
                if i==len(self.staged_tags):self.staged_tags.append(t)
                else:self.staged_tags[i]=t
            else:
                t=self.staged_tags[i];t.set_position((tx,ty));t.set_text(label_text)
                t.get_bbox_patch().set_edgecolor(edge_color);t.set_visible(True)
            t.set_color(edge_color)
        for t in self.staged_tags[len(tag_items):]:t.set_visible(False)
        self.problems.set_segments(red);self.problems.set_visible(bool(red))
        self.low_problems.set_segments(low_red);self.low_problems.set_visible(bool(low_red))
        self.warning.set_paths(warnings);self.warning.set_visible(bool(warnings))
        self.cuts.set_segments(first);self.cuts.set_visible(bool(first));self.second_cuts.set_segments(second);self.second_cuts.set_visible(bool(second))
        self.points.set_data(np.array(dots).T if dots else [[],[]]);self.points.set_visible(bool(dots))
        self.second_points.set_data(np.array(far_dots).T if far_dots else [[],[]]);self.second_points.set_visible(bool(far_dots))
        self.draw_labels()

    def draw_labels(self):
        w=self.w;selected=set(getattr(getattr(w,'selection',None),'selected',()));candidates=[];boxes=[]
        bank=self.risk_bank();nominal=bool((w.current_frame or {}).get('nominal_global'))
        hidden_outside=0
        if self.active() and selected:
            from .width_witness import pose_text
            labels={'red':'线路无法保持连通','yellow':'线路连通，有 DRC 违例','green':'通过检查','pending':'核验尚未完成'}
            vx0,vy0,vx1,vy1=w.view_bounds
            vcx,vcy=(vx0+vx1)*.5,(vy0+vy1)*.5
            visible_zones=[]
            for key,(q,status,g) in self.zone_views.items():
                if (q['layer'],q['index']) not in selected or (status=='green' and not q.get('routing_recovered')):continue
                if g.is_empty:g=q['source']
                if g.is_empty:continue
                gb=g.bounds
                if not _in_view(gb,w.view_bounds):
                    hidden_outside+=1;continue
                cx,cy=(gb[0]+gb[2])*.5,(gb[1]+gb[3])*.5
                visible_zones.append(((-RANK.get(status,0),(cx-vcx)**2+(cy-vcy)**2),key,q,status,g,gb))
            visible_zones.sort(key=lambda item:item[0])
            rules=(self.data() or {}).get('rules',{})
            places=w.view_settings.width_yield_decimals
            for _,key,q,status,g,gb in visible_zones[:32]:
                row=(bank or {}).get('zones',{}).get(key,{})
                kinds=q.get('problem_reasons',()) if not nominal else row.get('kinds',q.get('problem_reasons',()))
                if not kinds and status=='yellow':kinds=row.get('kinds',())
                text=f"{q['layer']} #{q['index']+1} · DIE{q['die']+1} · {labels[status]}"
                text+='\n'+('、'.join(kinds) if kinds else q.get('warning') or '核验尚未完成')
                if q.get('routing_note') and kinds:text+='\n'+q['routing_note']
                if rules:text+=f"\n要求线宽≥{rules['width_um']:g} μm，线距≥{rules['space_um']:g} μm"
                angles=q.get('boundary_angles',(0.,0.));text+=f"\n边界角度 {math.degrees(angles[0]):.1f}° / {math.degrees(angles[1]):.1f}°"
                probability=row.get('probability')
                if probability:
                    if probability.get('certified'):text+=f"\n失效概率 ≤ {probability['upper']*100:.{places}g}%（保守界限）"
                    else:text+=f"\n极端代表网格估计≈{probability.get('estimate',0)*100:.{places}g}% · 连续概率待定"
                witness=row.get('witness')
                if witness and (nominal or not q.get('problem_reasons')):text+='\n代表工况：'+pose_text(bank['grid_key'],witness['index'])
                else:
                    state=w.current_frame['states'][q['die']];text+=f"\nDX {state[0]:.2f} μm, DY {state[1]:.2f} μm, θ {math.degrees(state[2]):.2f}°"
                if self.zone_low.get(key):
                    text+='\n低概率工况（低于良率级别要求，已低亮显示）'
                candidates.append((status,text));boxes.append(shapely.box(*gb))
        from .width_labels import LabelLayout
        from .canvas_callouts import put
        from matplotlib.colors import to_rgb
        count=0
        if candidates:
            size=w.view_settings.width_label_font_pt;layout=LabelLayout(w.ax,boxes,size,self.text_sizes)
            if w.legend is not None and w.legend.get_visible():
                b=w.legend.get_window_extent(w.canvas.get_renderer());layout.labels.append((np.array([b.x0,b.y0])/layout.scale,np.array([b.x1,b.y1])/layout.scale))
            allow_pinned=len(candidates)<=4
            max_labels=16
            for i,(status,text) in enumerate(candidates):
                if count>=max_labels:break
                near=layout.place_selected(i,text)
                position=(*near,size) if near else (layout.place_pinned(i,text) if (allow_pinned or count==0) else None)
                if position is None:continue
                _,tip,offset,font=position;color=PALETTE[status]
                put(w,self.labels,count,text,tip,offset,font,color,np.asarray(to_rgb(color))*.64,color);count+=1
        for a in self.labels[count:]:a.set_visible(False)
    def pick(self,x,y,tolerance,*,handles_only=False):
        if not self.active():return None
        point=shapely.Point(x,y)
        hit_geometry=getattr(self,'hit_geometry',None)
        if hit_geometry is not None:
            best=None
            for index in hit_geometry.query(shapely.box(x-tolerance,y-tolerance,x+tolerance,y+tolerance)):
                q,kind,g=self.hit_items[int(index)]
                for xy in _lines(g):
                    distance=_segment_distance(xy,x,y)
                    if best is None or distance<best[0]:best=(distance,q,xy,kind)
            if best and best[0]<=tolerance:return best[1:]
        if handles_only:return None
        for q in self.pieces():
            pr=q.get('problem_region') if self.zone_views.get(zone_key(q),(None,'pending'))[1]!='green' else None
            if pr is not None and not pr.is_empty:
                b=pr.bounds
                if b[0]<=x<=b[2] and b[1]<=y<=b[3] and pr.covers(point):return q,None,'problem'
            br=q['bridge']
            if not br.is_empty:
                b=br.bounds
                if b[0]<=x<=b[2] and b[1]<=y<=b[3] and br.covers(point):return q,None,'bridge'
        return None
    def highlight(self,picked,linked=()):
        if self.hover is None or self.hover.axes is not self.w.ax:
            self.hover=Line2D([],[],color=CUT_COLOR,lw=4,alpha=.7,zorder=13);self.w.ax.add_line(self.hover)
        if picked is None or picked[2] in ('bridge','problem'):
            visible=self.hover.get_visible();self.hover.set_visible(False);self.pulse_timer.stop();self.hover_key=None
            if visible and hasattr(self.w,'scene_renderer'):self.w.render_scene()
            return False
        q,xy,kind=picked
        new_key=(self._key(q),kind)
        if self.hover_key==new_key and self.hover.get_visible() and self.w.cut_drag.state is None:
            return True
        xys=[xy,*linked]
        self.hover.set_data(np.concatenate([np.vstack((segment,[np.nan,np.nan])) for segment in xys]).T if linked else xy.T)
        self.hover.set_visible(True)
        self.hover.set_color(SECOND_CUT_COLOR if kind=='extra' else CUT_COLOR)
        points=[];offset=0
        for segment in xys:
            if np.linalg.norm(segment[-1]-segment[0])<1e-8:points.append(offset)
            offset+=len(segment)+1
        self.hover.set_marker('o' if points else '');self.hover.set_markevery(points or None);self.hover.set_markersize(9)
        self.hover_key=new_key
        if not self.pulse_timer.isActive():self.pulse_timer.start()
        return True
    def pulse(self):
        if self.hover is None or not self.hover.get_visible() or not self.active() or self.w._closing:
            self.pulse_timer.stop();return
        wave=.5+.5*math.sin(time.monotonic()*7)
        self.hover.set_alpha(.68+.12*wave);self.hover.set_linewidth(2.5+.35*wave)
        self.hover.set_markersize(9.+.6*wave)
        self.w.scene_renderer.draw_feedback(self.hover,(id(self.data()),self.stamp),self.w.legend)
    def clear_hover(self):self.highlight(None)
    def hide(self):
        self._cancel_stage_worker();self._stage_ref_keys={}
        self.w.cut_drag.retained=None
        self.staged_overlay=None;self._stage_frame=None;self.between_views={}
        self.clear_hover();self.stamp=None;self.handles={};self.zone_views={};self.zone_clips={};self.hit_items=[];self.hit_geometry=None
        for artist in [*self.artists.values(),*self.stationary_artists.values(),*self.labels,*self.staged_tags,*self.end_artists,*self.region_artists,*self.arrow_artists,*[getattr(self,n,None) for n in ('staged','warning','problems','low_problems','cuts','second_cuts','points','second_points')]]:
            if artist is not None:artist.set_visible(False)
        self.w.scene_renderer.external_draw(None)

    def release_artwork(self):
        """Forget detached artists and their geometry when replacing the drawing."""
        self.draft_frame=self.draft_data=self.draft_key=None
        self.artists.clear();self.stationary_artists.clear();self.path_cache.clear();self.union_cache.clear()
        for items in (self.labels,self.staged_tags,self.end_artists,self.region_artists,self.arrow_artists):items.clear()
        self.cuts=self.second_cuts=self.points=self.second_points=self.warning=self.problems=self.low_problems=self.staged=self.hover=None


class CutDrag:
    def __init__(self,window):
        self.w=window;self.state=None;self.line=None;self.latest=None
        self.worker=None;self.pending=None;self.retained=None
        self.timer=QtCore.QTimer(window);self.timer.setInterval(33);self.timer.timeout.connect(self._tick)
        for event,fn in [('button_press_event',self.press),('motion_notify_event',self.move),('button_release_event',self.release),('figure_leave_event',self.leave),('scroll_event',self.scroll)]:window.canvas.mpl_connect(event,fn)
    def _tolerance(self):
        t=self.w.ax.transData.inverted();return float(np.linalg.norm(t.transform((12,0))-t.transform((0,0))))
    def press(self,event):
        if getattr(self.w.canvas,"ruler_active",False) or getattr(getattr(self.w,'region_select',None),'mode',None) is not None:return
        if getattr(self.w,"remote_locked",False):return
        w=self.w
        if event.button!=1:return
        if not w.reconstruction_toggle.isChecked() or event.inaxes is not w.ax or event.xdata is None or getattr(w.canvas,'die_move_active',False):return
        picked=w.reconstruction_overlay.pick(event.xdata,event.ydata,self._tolerance())
        if picked is None:return
        overlay=w.reconstruction_overlay
        region,xy,kind=picked
        from .branch_regions import editable_piece
        piece=editable_piece(region,(event.xdata,event.ydata),kind)
        if piece.get('route_plan') is not None:
            plan=piece['route_plan']
            if not w.view_settings.reconstruction_allow_moving_pad and plan.allows_pad_point:return
            if overlay.dragged_handle() is not None:
                # A provisional pointer marker can be ahead of the solver.
                # Once a merged draft is ready, its outer section, including
                # the other constituent's endpoint, is authoritative.
                from .reconstruction_edits import key
                saved=next((row for row in w.view_settings.reconstruction_intervals if row[:5]==key(plan)),None)
                if saved is not None:
                    part=plan.partition(saved[-2],saved[-1])
                    if part is not None:piece=dict(piece,interval=(part['a'],part['b']))
        overlay.selected=overlay._key(piece);overlay.selected_kind=kind
        if kind in ('bridge','problem'):return
        if event.button!=1:return
        if w.toolbar.mode:
            if 'pan' in str(w.toolbar.mode).lower():
                if getattr(w.toolbar,'_pan_info',None) is not None:w.toolbar.release_pan(event)
                w.toolbar.pan()
            else:w.toolbar.zoom()
        event.reconstruction_cut_handled=True
        resume=bool(w.preview_pending or any(not q.cancelled.is_set() for q in w.preview_workers) or w.transition.timer.isActive())
        engine=w.engine
        displayed=(w.current_frame or {}).get('reconstruction',{}).get('configuration')
        solver=getattr(engine,'reconstructors',{}).get(displayed,getattr(engine,'reconstructor',None))
        w.stop_animation();w.cancel_previews();w.constraint_feedback.cancel()
        w.preview_ticket+=1;w.preview_pending=None;w._prepared_motion=None
        self._cancel_live();self.retained=None
        if event.dblclick:self.clear(overlay._key(piece),piece,kind);return
        if getattr(w,'selection',None):
            w.selection.press=None
            if w.selection.rubber is not None:w.selection.rubber.remove();w.selection.rubber=None
        self.state=dict(piece=piece,kind=kind,start=np.array([event.x,event.y]),moved=False,settings=w.view_settings,
                        frame=w.current_frame,solver=solver,resume_preview=resume,updated=w.view_settings,part=None,
                        original_marker=shapely.LineString(xy) if np.linalg.norm(xy[-1]-xy[0])>1e-8 else shapely.Point(xy[0]))
        self.state['draft']=(overlay.draft_frame,overlay.draft_data,overlay.draft_key)
        # Drag from the actual press point, not the projection of a tilted or
        # widened handle. Absolute projection made one direction stay clamped.
        side=1 if kind=='extra' else 0;owner=piece['die']
        plan=piece['route_plan']
        self.state['node_snap_cache']={}
        peer_limit=None
        if getattr(plan,'ownership_domain',None) is not None:
            ref=(piece['layer'],piece['index'])
            rb=plan.route.bounds
            peers=[]
            for other in overlay.pieces():
                if (other['layer'],other['index'])!=ref or other.get('ignored'):continue
                if not (other['die']!=owner or (other.get('via_refs') and set(other['via_refs']).isdisjoint(piece['via_refs']))):continue
                for g in (other['nominal_regions']['A'],other['nominal_regions']['C']):
                    if not g.is_empty:
                        gb=g.bounds
                        if gb[0]<=rb[2] and gb[2]>=rb[0] and gb[1]<=rb[3] and gb[3]>=rb[1]:peers.append(g)
            if peers:
                from .reconstruction_route import terminal_marks
                marks=terminal_marks(plan.route,shapely.union_all(peers))
                if len(marks):peer_limit=max(piece['interval'][0]+plan.tolerance*16,float(marks.min())-max(plan.tolerance*16,1e-3))
        self.state['peer_limit']=peer_limit
        self.state['press_world']=np.array([event.xdata,event.ydata])
        from .reconstruction_edits import key
        linked,_=overlay.linked_regions(region)
        handles=[];seen=set()
        primary=plan.tangent(piece['interval'][side])
        for other_region in linked:
            if other_region is region:
                main,other_side,visible=piece,side,self.state['original_marker']
            else:
                other_plan=other_region.get('route_plan')
                if other_plan is None:continue
                tangent=other_plan.tangent(sum(other_region['interval'])/2)
                other_side=side if primary@tangent>=0 else 1-side
                visible=other_region.get('extra_cut' if other_side else 'moved_cut')
                if visible is None or visible.is_empty:continue
                main=editable_piece(other_region,visible.centroid.coords[0],('extra' if other_side else 'moved'))
            members=[main,*main.get('drag_members',())]
            controls=[]
            for member in members:
                route=member['route_plan'];identity=key(route)
                if identity in seen:continue
                seen.add(identity)
                station=member['interval'][other_side]
                origin=np.asarray(route.route.interpolate(station).coords[0])
                controls.append(dict(piece=member,identity=identity,press=station,origin=origin,
                                     projection=station,cursor=origin.copy()))
            if controls:
                handles.append(dict(piece=main,side=other_side,kind='extra' if other_side else 'moved',
                                    ref=(main['layer'],main['index']),controls=controls,
                                    identities={control['identity'][-1] for control in controls},original_marker=visible))
        self.state['edited_handles']=handles
        self.state['refs']={handle['ref'] for handle in handles}
        w.canvas.reconstruction_drag_active=True
        self.latest=None;self.timer.start();overlay.highlight(picked)
        w.statusBar().showMessage('拖动共用动区边界；另一端保持不动，松开后更新所有工况。')
    def move(self,event):
        if self.state is None:
            if self.w.reconstruction_toggle.isChecked() and event.inaxes is self.w.ax and event.xdata is not None:self.w.reconstruction_overlay.highlight(self.w.reconstruction_overlay.pick(event.xdata,event.ydata,self._tolerance(),handles_only=True))
            else:self.w.reconstruction_overlay.clear_hover()
            return
        if self.w.navigation.drag is not None:return
        if event.xdata is None or event.ydata is None:return
        if not self.state['moved'] and np.linalg.norm(np.array([event.x,event.y])-self.state['start'])<2:return
        self.state['moved']=True;self.state['rotated_only']=False;self.latest=np.array([event.xdata,event.ydata])
    def _tick(self):
        state=self.state
        if state is None or self.latest is None:return
        world=self.latest;self.latest=None
        try:
            self._move_boundaries(state,world)
        except (ValueError,shapely.errors.GEOSException) as exc:
            if state.get('last_drag_warning')!=str(exc):
                state['last_drag_warning']=str(exc)
                self.w.log_bus.emit(f'重构边界保留上一次有效草稿：{exc}','WARNING')
        except Exception:
            from traceback import format_exc
            self.w.log_bus.emit('拖动动区已回撤\n'+format_exc(),'ERROR')
            self.cancel()
            self.w.error('本次拖动未完成，已恢复操作前状态。详情见运行信息。')

    def _move_boundaries(self,state,world):
        from .reconstruction_edits import apply_angles,rotate
        from .reconstruction_snap import node_snap
        from .selection import inverse_geometry
        options=state['settings'];frame=state['frame'];cfg=state['updated']
        displacement=world-state['press_world']
        pixel_um=abs(self.w.ax.get_xlim()[1]-self.w.ax.get_xlim()[0])/max(1.,self.w.ax.bbox.width)
        radius=options.reconstruction_node_snap_um
        if radius==0:radius=5*pixel_um
        preview=[];updates={}
        for item in state['edited_handles']:
            side=item['side'];primary_part=None
            for control in item['controls']:
                piece=control['piece'];route=piece['route_plan']
                center,pose=boundary_pose(piece,frame,side)
                origin=control['origin']
                basis=transform(shapely.Point(origin),center,pose)
                cursor=np.asarray(inverse_geometry(shapely.Point(np.asarray(basis.coords[0])+displacement),center,pose).coords[0])
                projected=route.drag_projection(cursor,control['projection'],control['cursor'])
                control.update(projection=projected,cursor=cursor)
                target=projected
                # Free boxes retain their two nominal blade orientations. The
                # rectangle is used to associate routes even after a boundary
                # moves beyond its original range.
                explicit={(row[:5],row[5]):row[6] for row in cfg.reconstruction_angles}
                if piece.get('strict_box') is not None:
                    for endpoint in (0,1):
                        if (control['identity'],endpoint) not in explicit:
                            cfg=rotate(cfg,piece,endpoint,0.)
                    explicit={(row[:5],row[5]):row[6] for row in cfg.reconstruction_angles}
                angle=explicit.get((control['identity'],side))
                if angle is not None:
                    from .manual_regions import translated_station
                    target=translated_station(route,origin,cursor-origin,angle,projected)
                if radius>0 and options.reconstruction_node_snap_strength:
                    target=node_snap(route,target,radius,options.reconstruction_node_snap_strength,
                                     state['node_snap_cache'].setdefault(control['identity'],{}),piece.get('boundary_angles',(0.,0.))[side])
                if item is state['edited_handles'][0] and side and state.get('peer_limit') is not None:
                    target=min(target,state['peer_limit'])
                part,_=route.edit_pose(*piece['interval'],side,target,*relative_pose(piece,frame),
                                       options.reconstruction_style,cursor=cursor,corner_snap=radius)
                part=apply_angles(cfg,route,part)
                if part is None:return
                updates[control['identity']]=(*control['identity'],part['a'],part['b'])
                control['part']=part
                if primary_part is None:primary_part=part
            preview.append((item,primary_part))
        intervals={row[:5]:row for row in cfg.reconstruction_intervals}
        intervals.update(updates)
        state['updated']=replace(cfg,reconstruction_intervals=tuple(intervals.values()))
        self._show_dragged_boundaries(preview)

    def _show_dragged_boundaries(self,preview):
        state=self.state;w=self.w;overlay=w.reconstruction_overlay
        first=state.get('marker') is None
        segments=[]
        for item,part in preview:
            piece=item['piece'];side=item['side']
            face=part['far' if side else 'near']
            placement=boundary_pose(piece,state['frame'],side)
            if face.geom_type=='Point':
                pad_handle=piece['route_plan'].pad_handle(side)
                if not pad_handle.is_empty:face=pad_handle
            line=transform(face,*placement)
            item['marker']=line;item['part']=part
            segments.extend(_lines(line))
        if not segments:return
        item,part=preview[0]
        state['marker']=item['marker'];state['part']=part
        if first:overlay.draw()
        overlay.highlight((state['piece'],segments[0],state['kind']),segments[1:])
        overlay.pulse()
        if w.view_settings.reconstruction_live:self._live(part)

    def scroll(self,event):
        if self.state is None:return
        event.reconstruction_cut_handled=True
        # Apply a queued cursor move first. Otherwise the next timer tick can
        # paint the old boundary over the newly rotated one.
        if self.latest is not None:self._tick()
        if self.state is None:return
        state=self.state
        from .reconstruction_edits import rotate,apply_angles,blade_angles
        updated=state['updated'];preview=[];changed=[]
        intervals={row[:5]:row for row in updated.reconstruction_intervals}
        for item in state['edited_handles']:
            primary=None
            side=item['side']
            for control in item['controls']:
                piece=control['piece'];plan=piece['route_plan']
                part=control.get('part') or plan.partition(*piece['interval'],piece.get('boundary_angles',(0.,0.)))
                if part is None:return
                current=dict(piece,interval=(part['a'],part['b']),boundary_angles=part.get('angles',(0.,0.)))
                if piece.get('strict_box') is not None:
                    updated=rotate(updated,current,1-side,0.)
                updated=rotate(updated,current,side,-math.radians(2)*event.step)
                angled=apply_angles(updated,plan,part)
                target,_=blade_angles(updated,plan,part['a'],part['b'],part.get('angles',(0.,0.)))
                if angled is None or abs(angled['angles'][side]-target[side])>1e-6:
                    self.w.statusBar().showMessage('该角度无法分离原始连接端，已保留上一个有效角度')
                    return
                if primary is None:primary=angled
                changed.append((control,angled))
                identity=control['identity']
                intervals[identity]=(*identity,angled['a'],angled['b'])
            preview.append((item,primary))
        for control,part in changed:control['part']=part
        state.update(updated=replace(updated,reconstruction_intervals=tuple(intervals.values())),moved=True,rotated_only=True)
        self._show_dragged_boundaries(preview)
        side=preview[0][0]['side'];part=preview[0][1]
        self.w.statusBar().showMessage(f"边界旋转 {math.degrees(part['angles'][side]):.1f}°；右键可撤销旋转")

    def _live(self,part):
        state=self.state
        if state['solver'] is None:return
        from .reconstruction_route import part_key
        key=(state['updated'].reconstruction_key(),part_key(part))
        if state.get('live_key')==key:return
        state['live_key']=key
        self.pending=(state,key,state['updated'],state['solver'],tuple(row.get('marker') for row in state['edited_handles']))
        self._start_live()

    def _start_live(self):
        if self.worker is not None or self.pending is None or self.w._closing:return
        from .gui import Worker
        from .resources import serial_scope
        from .reconstruction_zones import live_serial
        state,key,settings,solver,markers=self.pending
        self.pending=None
        original=state['frame']
        draft_frame,draft_data,_=state['draft']
        base=dict(original,reconstruction=draft_data) if draft_frame is original and draft_data is not None else original
        refs=state['refs']

        def work(_):
            frame=base
            with serial_scope():
                for ref in sorted(refs):
                    frame=live_serial(solver,settings,frame,ref,draft=True)
            return frame

        worker=Worker(work,self.w)
        self.worker=worker
        failed=False

        def accepted(frame):
            active=self.state is state
            if worker.cancelled.is_set() or self.w._closing or not (active or self.retained is state):return
            if key!=state.get('live_key'):return
            if not active and key[0]!=self.w.view_settings.reconstruction_key():return
            current=self.w.current_frame
            if current is not original and current is not state.get('live_frame'):return
            for handle,marker in zip(state['edited_handles'],markers):
                if marker is not None:handle['painted_marker']=marker
            frame['reconstruction']['draft_refs']=tuple(sorted(refs|set(base['reconstruction'].get('draft_refs',()))))
            if self.w.view_settings.reconstruction_auto:
                if not active:return
                frame=dict(frame,drc=[],drc_locations=[],drc_pending=True)
                self.w.current_frame=frame
                state['live_frame']=frame
                self.w.fill_issues();self.w.drc_overlay.draw()
            else:
                overlay=self.w.reconstruction_overlay
                overlay.draft_frame=current
                overlay.draft_key=settings.reconstruction_key()
                overlay.draft_data=frame['reconstruction']
            state['live_result_key']=key
            state['live_result']=frame['reconstruction']
            overlay=self.w.reconstruction_overlay
            overlay.draw()
            self.w.selection.draw(redraw=False)
            self.w.render_scene()

        def finished():
            self.worker=None
            worker.deleteLater()
            if self.w._closing:
                QtCore.QTimer.singleShot(0,self.w.close)
            else:
                self._start_live()
                if failed and self.worker is None and self.retained is state and self.w.reconstruction_pending:
                    self.w.reconstruction_overlay.stage(after_failed_drag=True)

        def report(message):
            nonlocal failed
            failed=True
            self.w.log_bus.emit('拖动预演：'+message,'ERROR')

        worker.done.connect(accepted)
        worker.failed.connect(report)
        worker.finished.connect(finished)
        worker.start()

    def _cancel_live(self):
        self.pending=None
        if self.worker is not None:self.worker.cancelled.set()

    def release(self,event):
        if self.state is None or event.button!=1:return
        if event.xdata is not None and event.ydata is not None and self.state['moved'] and not self.state.get('rotated_only'):
            self.latest=np.array([event.xdata,event.ydata])
        self._tick();state=self.state;w=self.w;overlay=w.reconstruction_overlay
        if state is None:return
        w.canvas.reconstruction_drag_active=False
        self.state=None;self.latest=None;self.timer.stop()
        updated=state['updated']
        if state.get('live_result_key') and state['live_result_key']==state['live_key']:
            from .reconstruction_edits import commit_merges
            updated=commit_merges(updated,{'reconstruction':state['live_result']},refs=state['refs'])
        changed=updated.reconstruction_key()!=w.view_settings.reconstruction_key()
        if not changed:
            self._cancel_live();overlay.clear_hover();overlay.draw();w.render_scene()
            if not state['moved'] and event.inaxes is w.ax and event.xdata is not None:
                # A 12 px handle target can cover a very short rigid strip.
                # Actual movement is a drag; a stationary click still selects
                # the physical strip underneath that generous hit target.
                point=(event.xdata,event.ydata)
                if w.selection.find_between_block(point) is not None:
                    w.selection.click(point,(event.x,event.y))
            if state['resume_preview']:w.request_preview(quiet=True,pose_change=True)
            return
        self.retained=state
        if updated.reconstruction_auto:
            self._cancel_live()
        if w._reconstruction_edit is None:
            w.begin_reconstruction_edit(w.view_settings,'拖动动区')
            w._reconstruction_edit['frame']=state['frame']
        w.apply_reconstruction_edit(updated,'拖动动区')
        overlay.clear_hover();overlay.draw();w.render_scene()

    def cancel(self,redraw=True):
        self._cancel_live()
        self.retained=None
        if self.state is None:return False
        w=self.w;overlay=w.reconstruction_overlay
        w.canvas.reconstruction_drag_active=False
        original=self.state['frame'];saved=self.state['draft'];resume=self.state['resume_preview']
        self.state=None;self.latest=None;self.timer.stop()
        w.current_frame=original
        overlay.draft_frame,overlay.draft_data,overlay.draft_key=saved
        overlay.clear_hover();overlay.stamp=None
        if redraw:
            overlay.draw();w.fill_issues();w.drc_overlay.draw();w.render_scene()
            if resume:w.request_preview(quiet=True,pose_change=True)
        return True
    def leave(self,event=None):
        if self.state is None:self.w.reconstruction_overlay.clear_hover()
    def clear(self,key,piece,kind='cut'):
        which=1 if kind=='extra' else 0;plan=piece.get('route_plan')
        if plan is None:return
        a,b=piece['interval'];target=plan.nominal_second if which else plan.nominal_first
        settings=_store_cut(self.w.view_settings,piece,None,plan.route.interpolate(target).coords[0],'extra' if which else 'cut')
        if settings==self.w.view_settings:return
        record(self.w)
        self.w.view_settings=settings;self.w.invalidate_reconstruction_edit();self.w.request_preview(quiet=True)


def _buttons(window,enabled):
    for name in ('cut_left','cut_right'):
        if hasattr(window,name):getattr(window,name).setEnabled(bool(enabled))


def _store_cut(settings,piece,normal,origin,kind='cut'):
    plan=piece.get('route_plan')
    if plan is None:return settings
    a,b=piece['interval'];which=1 if kind=='extra' else 0
    part,_=plan.edit(a,b,which,float(plan.route.project(shapely.Point(origin))))
    if part is None:return settings
    rows=[r for r in settings.reconstruction_intervals if (r[0],r[1],r[2],normalize_via_refs(r[3]),r[4])!=(*plan.key,plan.identity)]
    rows.append((*plan.key,plan.identity,part['a'],part['b']))
    return replace(settings,reconstruction_intervals=tuple(rows))


def nudge_cut(settings,piece,step,kind='cut'):
    plan=piece.get('route_plan')
    if plan is None:return settings
    value=piece['interval'][1 if kind=='extra' else 0]+step
    return _store_cut(settings,piece,None,plan.route.interpolate(max(0.,min(plan.length,value))).coords[0],kind)
class RegionSelect:
    """Draw an oriented selection and seed cuts on the intersected sources."""
    def __init__(self,window):
        self.w=window;self.mode=None;self.state=None;self.rect=None;self.cursor=None;self.marker=None;self.anchor_marker=None
        for name,fn in [('button_press_event',self.press),('motion_notify_event',self.move),('scroll_event',self.scroll),('axes_leave_event',self.leave),('figure_leave_event',self.leave)]:window.canvas.mpl_connect(name,fn)
    def set_mode(self,mode):
        if mode is not None and self.mode is None:
            self.previous_cursor=self.w.canvas.cursor()
            self.w.canvas.setCursor(getattr(Qt,'CursorShape',Qt).CrossCursor)
        if mode is None:
            if self.mode is not None:self.w.canvas.setCursor(self.previous_cursor)
            self._drop(clear_cursor=True)
        self.mode=mode
    def _ensure_markers(self):
        if self.marker is None or self.marker.axes is not self.w.ax:
            self.marker=Line2D([],[],marker='+',ms=10,mew=1.1,color='#263d52',ls='none',zorder=32,visible=False)
            self.w.ax.add_line(self.marker)
        if self.anchor_marker is None or self.anchor_marker.axes is not self.w.ax:
            self.anchor_marker=Line2D([],[],marker='+',ms=9,mew=1.1,color='#287bb2',ls='none',zorder=31,visible=False)
            self.w.ax.add_line(self.anchor_marker)
    def _drop(self,clear_cursor=False):
        self.w.canvas.region_rotating=False
        removed=False
        for attr in ('rect','anchor_marker') if not clear_cursor else ('rect','marker','anchor_marker'):
            artist=getattr(self,attr,None)
            if artist is not None:
                try:artist.remove()
                except ValueError:pass
                setattr(self,attr,None);removed=True
        if clear_cursor:self.cursor=None
        self.state=None
        if removed:
            self.w.scene_renderer.feedback_background=None
            self.w.canvas.draw_idle()
    def _point(self,event):
        return np.array([event.xdata,event.ydata],float) if event.xdata is not None and event.ydata is not None else None
    def vertices(self):
        s=self.state;c,t=np.cos(s['angle']),np.sin(s['angle']);rotation=np.array([[c,-t],[t,c]])
        x,y=(s['current']-s['anchor'])@rotation
        return s['anchor']+np.array([[0,0],[x,0],[x,y],[0,y]])@rotation.T
    def draw(self):
        self._ensure_markers()
        artists=[]
        if self.rect is not None and self.state is not None:
            self.rect.set_xy(self.vertices());self.rect.set_visible(True);artists.append(self.rect)
        if self.state is not None:
            self.anchor_marker.set_data([self.state['anchor'][0]],[self.state['anchor'][1]]);self.anchor_marker.set_visible(True);artists.append(self.anchor_marker)
        else:
            self.anchor_marker.set_visible(False)
        if self.cursor is not None and self.mode is not None:
            self.marker.set_data([self.cursor[0]],[self.cursor[1]]);self.marker.set_visible(True);artists.append(self.marker)
        else:
            self.marker.set_visible(False)
        self.w.scene_renderer.draw_feedback(artists or [self.marker],('region',id(self.rect),id(self.marker)),self.w.legend)
    def leave(self,event=None):
        if self.mode is None:return
        self.cursor=None
        if self.marker is not None and self.marker.get_visible():
            self.marker.set_visible(False);self.draw()
    def press(self,event):
        if self.mode is None or event.button!=1 or event.inaxes is not self.w.ax or getattr(event,'reconstruction_cut_handled',False):return
        if getattr(self.w,'remote_locked',False) or getattr(self.w.canvas,'ruler_active',False):return
        if self.w.shown_states is not None and np.any(self.w.shown_states):
            self.w.statusBar().showMessage('请等待名义零位复原后框选动区');return
        point=self._point(event)
        if point is None or not self.w.reconstruction_toggle.isChecked():return
        self.cursor=point
        selection=getattr(self.w,'selection',None)
        if selection is not None:
            selection.press=None
            if selection.rubber is not None:selection.rubber.remove();selection.rubber=None
        if self.state is not None:
            self._finish(point)
            return
        self.state=dict(anchor=point,current=point,angle=0.);self.w.canvas.region_rotating=True
        from matplotlib.patches import Polygon
        self.rect=Polygon(np.repeat(point[None,:],4,axis=0),facecolor='#6ca5ce',edgecolor='#287bb2',alpha=.25,lw=1,linestyle='--',zorder=20)
        self.w.ax.add_patch(self.rect);self.draw()
    def move(self,event):
        if self.mode is None or self.w.navigation.drag is not None:return
        if event.inaxes is not self.w.ax:self.leave();return
        point=self._point(event)
        if point is None:return
        self.cursor=point
        if self.state is not None:self.state['current']=point
        self.draw()
    def scroll(self,event):
        if self.state is None or self._point(event) is None:return
        point=self._point(event);delta=-math.radians(2)*event.step;c,t=math.cos(delta),math.sin(delta)
        self.state['anchor']=point+np.array([[c,-t],[t,c]])@(self.state['anchor']-point)
        self.state['current']=point;self.cursor=point;self.state['angle']+=delta;self.draw()
    def _finish(self,point):
        self.state['current']=point
        vertices=self.vertices();layers=self._layers();self._drop(clear_cursor=False)
        if abs(shapely.Polygon(vertices).area)<1e-12 or not layers:return
        settings=self.w.view_settings
        from .manual_regions import box_order
        mode=settings.reconstruction_box_mode
        field='reconstruction_angle_boxes' if mode=='aligned' else 'reconstruction_boxes'
        rows=(*getattr(settings,field),*((layer,tuple(tuple(map(float,v)) for v in vertices)) for layer in layers))
        geometry=self.w.geometry_cache.layers
        polygon=shapely.Polygon(vertices)
        def selected(ref):
            key,index=ref
            return key in layers and index<len(geometry[key]['geometry']) and geometry[key]['geometry'][index].intersects(polygon)
        reboxed={ref for ref in settings.reconstruction_box_excluded if selected(ref)}
        removed=tuple(ref for ref in settings.reconstruction_box_excluded if ref not in reboxed)
        unfollowed=tuple(ref for ref in settings.reconstruction_following if not selected(ref))
        order=box_order(settings)
        starts={(layer,index):start for layer,index,start in settings.reconstruction_box_starts}
        # Previously deleted boxes still apply to other conductors hit by the
        # same rectangle. Only these specific conductors start a new sequence.
        for ref in reboxed:
            if order:starts[ref]=len(order)
        # Additive & expanding box selection (V71): never wipe existing intervals,
        # angles or unrelated zones on the selected shape. Keep ignored shapes
        # marked in reconstruction_ignored so expand() seeds only the boxed zone(s).
        changes={field:rows,'reconstruction_box_order':(*order,*(mode for _ in layers)),
                 'reconstruction_box_starts':tuple((*ref,start) for ref,start in sorted(starts.items())),
                 'reconstruction_box_excluded':removed,'reconstruction_following':unfollowed}
        updated=replace(settings,**changes)
        if updated==settings:return
        if not self.w.apply_reconstruction_edit(updated,'框选动区'):return
        if self.w.view_settings.reconstruction_auto:
            self.w.statusBar().showMessage('已提交框选动区；必要时在原选框周围延伸至可用边界')
        else:
            self.w.statusBar().showMessage('已暂存框选动区并标记图形；点击“重构计算”统一计算')
    def _layers(self):
        return sorted(k for k,role in self.w.roles().items() if role=='固定RDL' and self.w.visible_boxes[k].isChecked())
