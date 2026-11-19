"""Nominal shared A/B/C regions and independently draggable source endpoints."""
from dataclasses import replace
import time
import math
import numpy as np
import shapely
from matplotlib.collections import LineCollection,PatchCollection
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle
from .reconstruction import normalize_via_refs,transform
from .qt_compat import Qt,QtCore,QtWidgets as W
from .reconstruction_events import zone_key,active_region,color_for,PALETTE,RANK

CUT_COLOR='#237ab9'
SECOND_CUT_COLOR=CUT_COLOR
WARNING_COLOR='#b570b8'


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


class ReconstructionOverlay:
    def __init__(self,window):
        self.draft_frame=self.draft_data=self.draft_key=None;self.w=window;self.artists={};self.cuts=None;self.stamp=self.table_stamp=None
        self.selected=self.selected_kind=None;self.hover=None;self.hover_key=None
        self.end_artists=[];self.region_artists=[];self.arrow_artists=[];self.path_cache={};self.handles={};self.union_cache={};self.labels=[];self.text_sizes={};self.zone_views={};self.zone_clips={}
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
    def stage(self):
        w=self.w;frame=w.current_frame
        if not frame or not frame.get('reconstruction') or not getattr(w,'engine',None):return
        cfg=w.view_settings;previous=w.preview_settings()
        if cfg.reconstruction_key()==previous.reconstruction_key():
            self.draft_frame=self.draft_data=self.draft_key=None;self.stamp=None;return
        if self.draft_frame is frame and self.draft_key==cfg.reconstruction_key():return
        refs=set();data=frame['reconstruction'];geo=w.engine.reconstructor.model.cache.layers
        for name in ('ignored','following','forced','angles','intervals','added','deleted','regions','boxes','angle_boxes','box_excluded','cuts','extra_cuts','second_cuts','dropped_cuts'):
            field='reconstruction_'+name
            for row in set(getattr(cfg,field))^set(getattr(previous,field)):
                if name in ('boxes','angle_boxes','regions'):
                    g=shapely.Polygon(row[1]) if name!='regions' else shapely.box(*row[1]);layer=row[0]
                    refs.update((layer,i) for i in np.flatnonzero(shapely.intersects(geo[layer]['geometry'],g)))
                else:refs.add(tuple(row[:2]))
        from .reconstruction_zones import live_serial
        local=frame
        for ref in sorted(refs):local=live_serial(w.engine.reconstructor,cfg,local,ref,draft=True)
        self.draft_frame=frame;self.draft_key=cfg.reconstruction_key();self.draft_data=dict(local['reconstruction'],draft_refs=refs or {(q['layer'],q['index']) for q in data['pieces']})
        self.stamp=None

    def pieces(self):return [q for q in (self.data() or {}).get('pieces',()) if self.active() and self.w.visible_boxes[q['layer']].isChecked()]
    def piece_for(self,key):return next((q for q in self.pieces() if self._key(q)==key),None)
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
                        staged.append(transform(part['far' if side else 'near'],frame['rotation_centers'][q['die']],frame['states'][q['die']]*q.get('endpoint_weights',(1.,0.))[side]));changed=True
                    if changed:g=shapely.union_all(staged)
                if g is None:continue
                if q.get('graph_members'):
                    # The merged zone owns this blade/dot. Its first member may
                    # start at an unrelated port on the other end of a plane.
                    result[key]=g
                elif plan is not None and g.geom_type=='Point':
                    local=plan.pad_handle(side)
                    local=transform(local,frame['rotation_centers'][q['die']],frame['states'][q['die']]*q.get('endpoint_weights',(1.,0.))[side])
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
        if frame is edit['frame'] or frame is edit.get('live_frame'):return edit
        drag.retained=None
        return None

    @staticmethod
    def owns_handle(q,kind,edit):
        if edit is None or edit['kind']!=kind:return False
        piece=edit['piece']
        return (q['layer'],q['index'])==(piece['layer'],piece['index']) and any(
            getattr(member.get('route_plan'),'identity',None)==piece['route_plan'].identity
            for member in q.get('graph_members',(q,)))

    def risk_bank(self):return getattr(getattr(self.w,'reconstruction_yield',None),'evidence',None)
    def draw(self):
        w=self.w
        if self.cuts is None or self.cuts.axes is not w.ax:
            self.artists={}
            self.staged=PatchCollection([],facecolor='#5992b62a',edgecolor='#44698c',linestyles='--',linewidths=1.2,zorder=10);w.ax.add_collection(self.staged)
            self.warning=PatchCollection([],facecolor='none',edgecolor=WARNING_COLOR,linestyles='--',linewidths=.8,zorder=9.4);w.ax.add_collection(self.warning)
            self.problems=PatchCollection([],facecolor='none',edgecolor='#b92d39',alpha=.7,linewidths=.9,zorder=9.7);w.ax.add_collection(self.problems)
            self.cuts=LineCollection([],colors=CUT_COLOR,linewidths=2.2,zorder=11.5);w.ax.add_collection(self.cuts)
            self.second_cuts=LineCollection([],colors=SECOND_CUT_COLOR,linewidths=2.2,zorder=11.6);w.ax.add_collection(self.second_cuts)
            self.points=Line2D([],[],ls='none',marker='o',markersize=8,color=CUT_COLOR,markeredgecolor='white',markeredgewidth=.8,zorder=12);w.ax.add_line(self.points)
            self.second_points=Line2D([],[],ls='none',marker='o',markersize=8,color=SECOND_CUT_COLOR,markeredgecolor='white',markeredgewidth=.8,zorder=12);w.ax.add_line(self.second_points)
            self.stamp=None
        data=self.data() if self.active() else None;bank=self.risk_bank() if self.active() else None
        vis=tuple(k for k,v in w.visible_boxes.items() if v.isChecked());order=tuple(w.ordered_layers())
        nominal=bool((w.current_frame or {}).get('nominal_global'))
        edit=self.dragged_handle() if data else None
        # During the gesture the moving handle is a single animated artist.
        # After release it is painted once in the static collection until the
        # committed frame takes ownership.
        dragging=edit is not None and w.cut_drag.state is edit
        edit_stamp=(id(edit),dragging,0 if dragging else id(edit['marker'])) if edit else None
        stamp=(id(data),id(bank),vis,w.view_bounds,order,nominal,edit_stamp,repr(w.layer_styles))
        if stamp==self.stamp:self.draw_labels();return
        if self.stamp is not None and self.stamp[0]!=id(data) and edit is None:
            if self.hover is not None:self.hover.set_visible(False)
            self.pulse_timer.stop();self.hover_key=None
        w.scene_renderer.feedback_background=None
        self.stamp=stamp;paths={};colors={};first=[];second=[];dots=[];far_dots=[];warnings=[];region_groups={};red=[]
        old_unions=self.union_cache;self.union_cache={};old_cache=self.path_cache;self.path_cache={};self.zone_views={}
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
        for (layer,index),g in copper.items():
            if _in_view(g.bounds,w.view_bounds):
                region_groups[layer,index,'B','following' if (layer,index) in following else '']={id(g):g}
        verified=bool((data or {}).get('constraints_complete'))
        unchecked=set((data or {}).get('draft_refs',()))
        if bank and bank.get('configuration')!= (data or {}).get('configuration'):bank=None
        for q in current:
            ref=(q['layer'],q['index'])
            edited=bool(edit and ref==(edit['piece']['layer'],edit['piece']['index']))
            status=color_for(q,bank,nominal,checked=verified or bool(unchecked and ref not in unchecked))
            if ref in unchecked or (edited and (dragging or not verified)):status='pending'
            gactive=active_region(q)
            material=copper.get(ref)
            if material is not None and not gactive.is_empty and not q.get('connection_failed'):
                key=(id(material),id(gactive))
                clip=old_clips.get(key)
                if clip is None:clip=(material,gactive,gactive if material.covers(gactive) else material.intersection(gactive))
                self.zone_clips[key]=clip;gactive=clip[2]
            self.zone_views[zone_key(q)]=(q,status,gactive)
            if not gactive.is_empty and _in_view(gactive.bounds,w.view_bounds):
                region_groups.setdefault((*ref,'A',status),{})[id(gactive)]=gactive
                if status=='red':red.append(patch(gactive))
                if q.get('routing_recovered'):warnings.append(patch(gactive))
            if q.get('box_invalid'):continue
            for kind,target,dot_list,line_list in (('moved','moved_cut',dots,first),('extra','extra_cut',far_dots,second)):
                face=self.handles.get((self._key(q),kind),q.get(target))
                if self.owns_handle(q,kind,edit):
                    if face is None:continue
                    components=list(shapely.get_parts(face))
                    if components:
                        marker=(edit.get('painted_marker') if ref in unchecked else None) or edit['original_marker']
                        edited_component=min(range(len(components)),key=lambda i:components[i].distance(marker))
                        del components[edited_component]
                    face=shapely.GeometryCollection(components)
                if face is not None and not face.is_empty:
                    if q.get('route_plan') is not None:self.hit_items.append((q,kind,face))
                    for component in shapely.get_parts(face):
                        if component.geom_type in ('Point','MultiPoint'):dot_list.extend(shapely.get_coordinates(component))
                        else:line_list.extend(_lines(component))
        if edit is not None and not dragging:
            face=edit['marker'];kind=edit['kind']
            self.hit_items.append((edit['piece'],kind,face))
            if face.geom_type=='Point':(far_dots if kind=='extra' else dots).append(face.coords[0])
            else:(second if kind=='extra' else first).extend(_lines(face))
        self.hit_geometry=shapely.STRtree([g for _,_,g in self.hit_items])
        for (layer,index,kind,status),values in sorted(region_groups.items(),key=lambda item:RANK.get(item[0][3],-1)):
            geos=tuple(values.values());cache_key=(layer,index,kind,status,tuple(values));previous=old_unions.get(cache_key)
            g=previous[1] if previous is not None and all(a is b for a,b in zip(previous[0],geos)) else (geos[0] if len(geos)==1 else shapely.union_all(geos))
            from matplotlib.colors import to_rgba
            color=PALETTE[status] if kind=='A' else '#dfd5ed' if status=='following' else w.style_for(layer)['color']
            alpha=.66 if kind=='A' else .20 if status=='following' else w.style_for(layer)['alpha']
            self.union_cache[cache_key]=(geos,g);paths.setdefault((layer,kind),[]).append(patch(g));colors.setdefault((layer,kind),[]).append(to_rgba(color,alpha))
        for key in paths:
            if key not in self.artists:
                artist=PatchCollection([],linewidths=0);w.ax.add_collection(artist);self.artists[key]=artist
        for (layer,k),artist in self.artists.items():
            shapes=paths.get((layer,k),[]);artist.set_paths(shapes);artist.set_visible(bool(shapes));artist.set_facecolors(colors.get((layer,k),[]));artist.set_edgecolors('none')
            artist.set_alpha(None)
            artist.set_zorder(2+(order.index(layer) if layer in order else 0)/max(1,len(order))+.001*('BCA'.index(k)+1))
        staged=[]
        for layer,index in (data or {}).get('draft_refs',()):
            if layer in vis:
                gs=[g for (l,i,kind,status),parts in region_groups.items() if (l,i)==(layer,index) and kind=='A' for g in parts.values()]
                if not gs:gs=[w.engine.reconstructor.model.cache.layers[layer]['geometry'][index]]
                staged.extend(patch(g) for g in gs if not g.is_empty and _in_view(g.bounds,w.view_bounds))
        self.staged.set_paths(staged);self.staged.set_visible(bool(staged))
        self.problems.set_paths(red);self.problems.set_visible(bool(red));self.warning.set_paths(warnings);self.warning.set_visible(bool(warnings))
        self.cuts.set_segments(first);self.cuts.set_visible(bool(first));self.second_cuts.set_segments(second);self.second_cuts.set_visible(bool(second))
        self.points.set_data(np.array(dots).T if dots else [[],[]]);self.points.set_visible(bool(dots))
        self.second_points.set_data(np.array(far_dots).T if far_dots else [[],[]]);self.second_points.set_visible(bool(far_dots))
        self.draw_labels()

    def draw_labels(self):
        w=self.w;selected=set(getattr(getattr(w,'selection',None),'selected',()));items=[];boxes=[];outside=[];anchors=[]
        bank=self.risk_bank();nominal=bool((w.current_frame or {}).get('nominal_global'))
        if self.active():
            from .width_witness import pose_text
            labels={'red':'线路无法保持连通','yellow':'线路连通，有 DRC 违例','green':'通过检查','pending':'核验尚未完成'}
            for key,(q,status,g) in self.zone_views.items():
                if (q['layer'],q['index']) not in selected or (status=='green' and not q.get('routing_recovered')):continue
                if g.is_empty:g=q['source']
                row=(bank or {}).get('zones',{}).get(key,{})
                kinds=q.get('problem_reasons',()) if not nominal else row.get('kinds',q.get('problem_reasons',()))
                if not kinds and status=='yellow':kinds=row.get('kinds',())
                text=f"{q['layer']} #{q['index']+1} · DIE{q['die']+1} · {labels[status]}"
                text+='\n'+('、'.join(kinds) if kinds else q.get('warning') or '核验尚未完成')
                if q.get('routing_note') and kinds:text+='\n'+q['routing_note']
                rules=(self.data() or {}).get('rules',{})
                if rules:text+=f"\n要求线宽≥{rules['width_um']:g} μm，线距≥{rules['space_um']:g} μm"
                angles=q.get('boundary_angles',(0.,0.));text+=f"\n边界角度 {math.degrees(angles[0]):.1f}° / {math.degrees(angles[1]):.1f}°"
                probability=row.get('probability');places=w.view_settings.width_yield_decimals
                if probability:
                    if probability.get('certified'):text+=f"\n失效概率 ≤ {probability['upper']*100:.{places}g}%（保守界限）"
                    else:text+=f"\n极端代表网格估计≈{probability.get('estimate',0)*100:.{places}g}% · 连续概率待定"
                witness=row.get('witness')
                if witness and (nominal or not q.get('problem_reasons')):text+='\n代表工况：'+pose_text(bank['grid_key'],witness['index'])
                else:
                    state=w.current_frame['states'][q['die']];text+=f"\nDX {state[0]:.2f} μm, DY {state[1]:.2f} μm, θ {math.degrees(state[2]):.2f}°"
                rect,anchor,out=w.width_overlay.label_anchor(g)
                if out:text+='\n动区在当前视野外'
                items.append((status,text));boxes.append(rect if out else shapely.box(*g.bounds));anchors.append(anchor);outside.append(out)
        from .width_labels import LabelLayout
        from .canvas_callouts import put
        from matplotlib.colors import to_rgb
        count=0
        if items:
            size=w.view_settings.width_label_font_pt;layout=LabelLayout(w.ax,boxes,size,self.text_sizes)
            if w.legend is not None and w.legend.get_visible():
                b=w.legend.get_window_extent(w.canvas.get_renderer());layout.labels.append((np.array([b.x0,b.y0])/layout.scale,np.array([b.x1,b.y1])/layout.scale))
            for i,(status,text) in enumerate(items):
                near=layout.place_selected(i,text);position=(*near,size) if near else layout.place_pinned(i,text)
                if position is None:continue
                if outside[i]:position=w.width_overlay.retarget(position,anchors[i])
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
            if q.get('problem_region') is not None and q['problem_region'].covers(point):return q,None,'problem'
            if q['bridge'].covers(point):return q,None,'bridge'
        return None
    def highlight(self,picked):
        if self.hover is None or self.hover.axes is not self.w.ax:
            self.hover=Line2D([],[],color=CUT_COLOR,lw=4,alpha=.7,zorder=13);self.w.ax.add_line(self.hover)
        if picked is None or picked[2] in ('bridge','problem'):
            visible=self.hover.get_visible();self.hover.set_visible(False);self.pulse_timer.stop();self.hover_key=None
            if visible and hasattr(self.w,'scene_renderer'):self.w.render_scene()
            return False
        q,xy,kind=picked;self.hover.set_data(xy.T);self.hover.set_visible(True)
        self.hover.set_color(SECOND_CUT_COLOR if kind=='extra' else CUT_COLOR)
        self.hover.set_marker('o' if np.linalg.norm(xy[-1]-xy[0])<1e-8 else '');self.hover.set_markersize(9)
        self.hover_key=(self._key(q),kind)
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
        self.w.cut_drag.retained=None
        self.clear_hover();self.stamp=None;self.handles={};self.zone_views={};self.zone_clips={};self.hit_items=[];self.hit_geometry=None
        for artist in [*self.artists.values(),*self.labels,*self.end_artists,*self.region_artists,*self.arrow_artists,*[getattr(self,n,None) for n in ('staged','warning','problems','cuts','second_cuts','points','second_points')]]:
            if artist is not None:artist.set_visible(False)
        self.w.scene_renderer.external_draw(None)


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
        piece,xy,kind=picked
        from .branch_regions import editable_piece
        piece=editable_piece(piece,(event.xdata,event.ydata),kind)
        if piece.get('route_plan') is not None:
            plan=piece['route_plan']
            saved=next((row for row in w.view_settings.reconstruction_intervals if (row[0],row[1],row[2],normalize_via_refs(row[3]),row[4])==(*plan.key,plan.identity)),None)
            if saved is not None:
                part=plan.partition(saved[-2],saved[-1])
                if part is not None:piece=dict(piece,interval=(part['a'],part['b']))
        overlay=w.reconstruction_overlay;overlay.selected=overlay._key(piece);overlay.selected_kind=kind
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
                        frame=w.current_frame,solver=solver,resume_preview=resume,updated=w.view_settings,part=None,original_marker=shapely.LineString(xy) if np.linalg.norm(xy[-1]-xy[0])>1e-8 else shapely.Point(xy[0]),
                        handle_offset=(xy.mean(axis=0)-np.asarray(piece['extra_cut' if kind=='extra' else 'moved_cut'].coords[0]) if piece['extra_cut' if kind=='extra' else 'moved_cut'].geom_type=='Point' else np.zeros(2)))
        self.state['draft']=(overlay.draft_frame,overlay.draft_data,overlay.draft_key)
        # Drag from the actual press point, not the projection of a tilted or
        # widened handle. Absolute projection made one direction stay clamped.
        from .selection import inverse_geometry
        side=1 if kind=='extra' else 0;frame=w.current_frame;owner=piece['die']
        point=np.array([event.xdata,event.ydata])-self.state['handle_offset']
        nominal=inverse_geometry(shapely.Point(point),frame['rotation_centers'][owner],frame['states'][owner]*piece.get('endpoint_weights',(1.,0.))[side])
        plan=piece['route_plan'];origin=np.asarray(plan.route.interpolate(piece['interval'][side]).coords[0])
        self.state['nominal_press']=np.asarray(nominal.coords[0])
        self.state['drag_offset']=origin-np.asarray(nominal.coords[0])
        self.state['press_projection']=piece['interval'][side]
        self.state['projection_cursor']=origin.copy();self.state['projection']=piece['interval'][side]
        self.state['press_interval']=piece['interval'][side]
        self.state['companions']=[]
        for member in piece.get('drag_members',()):
            other=member['route_plan']
            if other is plan:continue
            origin=np.asarray(other.route.interpolate(member['interval'][side]).coords[0])
            mark=float(np.clip(member['interval'][side]+(np.asarray(nominal.coords[0])-origin)@other.tangent(member['interval'][side]),0.,other.length))
            self.state['companions'].append(dict(piece=member,press=member['interval'][side],projection=member['interval'][side],cursor=origin.copy(),offset=origin-np.asarray(nominal.coords[0])))
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
        if state is None:return
        if self.latest is not None:
            cursor=self.latest-self.state['handle_offset'];self.latest=None;piece=state['piece'];owner=piece['die'];frame=state['frame']
            from .selection import inverse_geometry
            weight=piece.get('endpoint_weights',(1.,0.))[1 if state['kind']=='extra' else 0]
            cursor=np.asarray(inverse_geometry(shapely.Point(cursor),frame['rotation_centers'][owner],frame['states'][owner]*weight).coords[0])
            plan=piece.get('route_plan')
            if plan is not None:
                which=1 if state['kind']=='extra' else 0
                route_cursor=cursor+state['drag_offset']
                projection=plan.drag_projection(route_cursor,state['projection'],state['projection_cursor'])
                state['projection']=projection;state['projection_cursor']=route_cursor.copy()
                from .manual_regions import move_side
                edit=move_side(state['settings'],plan,state['nominal_press'],cursor,piece.get('strict_box')) if piece.get('strict_box') is not None else None
                if edit:state['updated'],part=edit
                else:
                    part,clamped=plan.edit_pose(*piece['interval'],which,state['press_interval']+projection-state['press_projection'],frame['rotation_centers'][owner],frame['states'][owner],self.w.view_settings.reconstruction_style,cursor=route_cursor)
                    if part is not None:
                        from .reconstruction_edits import apply_angles
                        part=apply_angles(state['updated'],plan,part)
                if part is not None:
                    rows=[r for r in self.w.view_settings.reconstruction_intervals if (r[0],r[1],r[2],normalize_via_refs(r[3]),r[4])!=(*plan.key,plan.identity)]
                    rows.append((*plan.key,plan.identity,part['a'],part['b']))
                    for companion in state['companions']:
                        member=companion['piece'];other=member['route_plan']
                        companion_cursor=cursor+companion['offset']
                        mark=other.drag_projection(companion_cursor,companion['projection'],companion['cursor'])
                        companion.update(projection=mark,cursor=companion_cursor.copy())
                        target=member['interval'][which]+mark-companion['press']
                        extra,_=other.edit_pose(*member['interval'],which,target,frame['rotation_centers'][owner],frame['states'][owner],self.w.view_settings.reconstruction_style,cursor=companion_cursor)
                        if extra is not None:
                            rows=[row for row in rows if row[:5]!=(*other.key,other.identity)]
                            rows.append((*other.key,other.identity,extra['a'],extra['b']))
                    state['updated']=replace(state['updated'],reconstruction_intervals=tuple(rows));state['part']=part
                    self._feedback(part)

    def _feedback(self,part):
        state=self.state;w=self.w;overlay=w.reconstruction_overlay
        piece=state['piece'];side=int(state['kind']=='extra');owner=piece['die'];frame=state['frame']
        face=part['far' if side else 'near']
        weight=piece.get('endpoint_weights',(1.,0.))[side]
        line=transform(face,frame['rotation_centers'][owner],frame['states'][owner]*weight)
        if line.geom_type=='Point':
            offset=state['handle_offset']
            if not np.any(offset):
                pad_handle=piece['route_plan'].pad_handle(side)
                if not pad_handle.is_empty:
                    line=transform(pad_handle,frame['rotation_centers'][owner],frame['states'][owner]*weight)
            else:line=shapely.Point(np.asarray(line.coords[0])+offset)
        segments=_lines(line)
        if segments:
            first=state.get('marker') is None
            state['marker']=line
            if first:overlay.draw()
            overlay.highlight((piece,segments[0],state['kind']))
            overlay.pulse()
        if w.view_settings.reconstruction_live:self._live(part)

    def scroll(self,event):
        if self.state is None:return
        event.reconstruction_cut_handled=True
        # Apply a queued cursor move first. Otherwise the next timer tick can
        # paint the old boundary over the newly rotated one.
        if self.latest is not None:self._tick()
        state=self.state;piece=state['piece'];side=1 if state['kind']=='extra' else 0
        from .reconstruction_edits import rotate,apply_angles
        plan=piece['route_plan'];part=state['part'] or plan.partition(*piece['interval'],piece.get('boundary_angles',(0.,0.)))
        if part is None:return
        current=dict(piece,interval=(part['a'],part['b']),boundary_angles=part.get('angles',(0.,0.)))
        updated=rotate(state['updated'],current,side,-math.radians(2)*event.step)
        angled=apply_angles(updated,plan,part)
        if angled is None:
            self.w.statusBar().showMessage('该角度无法分离原始连接端，已保留上一个有效角度');return
        state.update(updated=updated,part=angled,moved=True,rotated_only=True)
        self._feedback(angled)
        self.w.statusBar().showMessage(f"边界旋转 {math.degrees(angled['angles'][side]):.1f}°；右键可撤销旋转")

    def _live(self,part):
        state=self.state
        if state['solver'] is None:return
        from .reconstruction_route import part_key
        key=(state['updated'].reconstruction_key(),part_key(part))
        if state.get('live_key')==key:return
        state['live_key']=key
        self.pending=(state,key,state['updated'],state['solver'],state.get('marker'))
        self._start_live()

    def _start_live(self):
        if self.worker is not None or self.pending is None or self.w._closing:return
        from .gui import Worker
        from .resources import serial_scope
        from .reconstruction_zones import live_serial
        state,key,settings,solver,marker=self.pending
        self.pending=None
        original=state['frame']
        draft_frame,draft_data,_=state['draft']
        base=dict(original,reconstruction=draft_data) if draft_frame is original and draft_data is not None else original
        piece=state['piece']
        ref=(piece['layer'],piece['index'])

        def work(_):
            with serial_scope():
                return live_serial(solver,settings,base,ref,draft=True)

        worker=Worker(work,self.w)
        self.worker=worker

        def accepted(frame):
            active=self.state is state
            if worker.cancelled.is_set() or self.w._closing or not (active or self.retained is state):return
            if key!=state.get('live_key'):return
            current=self.w.current_frame
            if current is not original and current is not state.get('live_frame'):return
            state['painted_marker']=marker
            frame['reconstruction']['draft_refs']=(ref,)
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
                overlay.draft_data=dict(frame['reconstruction'],draft_refs=(ref,))
            state['live_result_key']=key
            state['live_result']=frame['reconstruction']
            overlay=self.w.reconstruction_overlay
            overlay.draw()
            self.w.render_scene()

        def finished():
            self.worker=None
            worker.deleteLater()
            if self.w._closing:
                QtCore.QTimer.singleShot(0,self.w.close)
            else:
                self._start_live()

        worker.done.connect(accepted)
        worker.failed.connect(lambda message:self.w.log_bus.emit('拖动预演：'+message,'ERROR'))
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
        w.canvas.reconstruction_drag_active=False
        self.state=None;self.latest=None;self.timer.stop()
        updated=state['updated']
        if state.get('live_result_key') and state['live_result_key']==state['live_key']:
            from .reconstruction_edits import commit_merges
            updated=commit_merges(updated,{'reconstruction':state['live_result']},ref=(state['piece']['layer'],state['piece']['index']))
        changed=updated.reconstruction_key()!=w.view_settings.reconstruction_key()
        if not changed:
            self._cancel_live();overlay.clear_hover();overlay.draw();w.render_scene()
            if state['resume_preview']:w.request_preview(quiet=True,pose_change=True)
            return
        self.retained=state
        w.view_settings=updated
        if updated.reconstruction_auto:
            self._cancel_live()
            w.invalidate_reconstruction_edit()
            w.request_preview(quiet=True)
        else:
            # The latest local preview can finish after mouse-up. Applying it
            # changes the drawing only, leaving the committed calculation intact.
            w.defer_reconstruction(stage=not updated.reconstruction_live)
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
        self.w=window;self.mode=None;self.state=None;self.rect=None
        for name,fn in [('button_press_event',self.press),('motion_notify_event',self.move),('scroll_event',self.scroll)]:window.canvas.mpl_connect(name,fn)
    def set_mode(self,mode):
        if mode is not None and self.mode is None:
            self.previous_cursor=self.w.canvas.cursor()
            self.w.canvas.setCursor(getattr(Qt,'CursorShape',Qt).CrossCursor)
        if mode is None:
            if self.mode is not None:self.w.canvas.setCursor(self.previous_cursor)
            self._drop()
        self.mode=mode
    def _drop(self):
        self.w.canvas.region_rotating=False
        if self.rect is not None:
            try:self.rect.remove()
            except ValueError:pass
            self.w.scene_renderer.feedback_background=None
            self.w.canvas.draw_idle()
        self.rect=None;self.state=None
    def _point(self,event):
        return np.array([event.xdata,event.ydata],float) if event.xdata is not None and event.ydata is not None else None
    def vertices(self):
        s=self.state;c,t=np.cos(s['angle']),np.sin(s['angle']);rotation=np.array([[c,-t],[t,c]])
        x,y=(s['current']-s['anchor'])@rotation
        return s['anchor']+np.array([[0,0],[x,0],[x,y],[0,y]])@rotation.T
    def draw(self):
        self.rect.set_xy(self.vertices());self.w.scene_renderer.draw_feedback(self.rect,('region',id(self.rect)),self.w.legend)
    def press(self,event):
        if self.mode is None or event.button!=1 or event.inaxes is not self.w.ax or getattr(event,'reconstruction_cut_handled',False):return
        if getattr(self.w,'remote_locked',False) or getattr(self.w.canvas,'ruler_active',False):return
        if self.w.shown_states is not None and np.any(self.w.shown_states):
            self.w.statusBar().showMessage('请等待名义零位复原后框选动区');return
        point=self._point(event)
        if point is None or not self.w.reconstruction_toggle.isChecked():return
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
        if self.state is not None and self._point(event) is not None:self.state['current']=self._point(event);self.draw()
    def scroll(self,event):
        if self.state is None or self._point(event) is None:return
        point=self._point(event);delta=-math.radians(2)*event.step;c,t=math.cos(delta),math.sin(delta)
        self.state['anchor']=point+np.array([[c,-t],[t,c]])@(self.state['anchor']-point)
        self.state['current']=point;self.state['angle']+=delta;self.draw()
    def _finish(self,point):
        self.state['current']=point
        vertices=self.vertices();layers=self._layers();self._drop()
        if abs(shapely.Polygon(vertices).area)<1e-12 or not layers:return
        field='reconstruction_angle_boxes' if self.w.view_settings.reconstruction_box_mode=='aligned' else 'reconstruction_boxes'
        rows=(*getattr(self.w.view_settings,field),*((layer,tuple(tuple(map(float,v)) for v in vertices)) for layer in layers))
        removed=set(self.w.view_settings.reconstruction_box_excluded)
        if removed:
            geo=self.w.geometry_cache.layers
            polygon=shapely.Polygon(vertices)
            removed={ref for ref in removed if ref[0] not in layers or ref[1]>=len(geo[ref[0]]['geometry'])
                     or not geo[ref[0]]['geometry'][ref[1]].intersects(polygon)}
        self.w.view_settings=replace(self.w.view_settings,**{field:rows},reconstruction_box_excluded=tuple(sorted(removed)))
        self.w.invalidate_reconstruction_edit();self.w.request_preview(quiet=True)
        self.w.statusBar().showMessage('已提交框选动区；必要时在原选框周围延伸至可用边界')
    def _layers(self):
        return sorted(k for k,role in self.w.roles().items() if role=='固定RDL' and self.w.visible_boxes[k].isChecked())
