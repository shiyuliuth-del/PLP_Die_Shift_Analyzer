"""Nominal shared A/B/C regions and independently draggable source endpoints."""
from dataclasses import replace
import time
import math
import numpy as np
import shapely
from matplotlib.collections import LineCollection,PatchCollection
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle
from .reconstruction import normalize_via_refs,cut_identity_matches,transform
from .qt_compat import QtCore,QtWidgets as W
from .reconstruction_events import zone_key,active_region,color_for,PALETTE,RANK

REGION_COLORS={'move':'#2f7bd6','stay':'#c2571f'}
REGION_LABELS={'move':'动区','stay':'静区'}
COLORS={'A':'#edbb77','B':'#b6c8dd','C':'#a5c7bd'}
CUT_COLOR='#237ab9'
SECOND_CUT_COLOR='#c2912e'
WARNING_COLOR='#b570b8'
DRAG_FIRST_CUT=True


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


class ReconstructionOverlay:
    def __init__(self,window):
        self.w=window;self.artists={};self.cuts=None;self.stamp=self.table_stamp=None
        self.selected=self.selected_kind=None;self.hover=None;self.hover_key=None
        self.end_artists=[];self.region_artists=[];self.arrow_artists=[];self.path_cache={};self.handles={};self.union_cache={};self.labels=[];self.text_sizes={};self.zone_views={}
        self.pulse_timer=QtCore.QTimer(window);self.pulse_timer.setInterval(33);self.pulse_timer.timeout.connect(self.pulse)
    def active(self):return self.w.reconstruction_toggle.isChecked()
    @staticmethod
    def _key(q):return q['layer'],int(q['index']),normalize_via_refs(q.get('via_refs',q['vias'])),q.get('route_identity')
    def data(self):return (self.w.current_frame or {}).get('reconstruction')
    def pieces(self):return [q for q in (self.data() or {}).get('pieces',()) if self.active() and self.w.visible_boxes[q['layer']].isChecked()]
    def piece_for(self,key):return next((q for q in self.pieces() if self._key(q)==key),None)
    def handle_geometries(self,pieces):
        frame=self.w.current_frame or {};result={};groups={}
        for q in pieces:
            plan=q.get('route_plan')
            for kind,side,name in (('moved',0,'moved_cut'),('extra',1,'extra_cut')):
                g=q.get(name);key=(self._key(q),kind)
                if g is None:continue
                if plan is not None and g.geom_type=='Point':
                    local=plan.pad_handle(side)
                    if side==0:local=transform(local,frame['rotation_centers'][q['die']],frame['states'][q['die']])
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
    def risk_bank(self):return getattr(getattr(self.w,'reconstruction_yield',None),'evidence',None)
    def draw(self):
        w=self.w
        if self.cuts is None or self.cuts.axes is not w.ax:
            self.artists={}
            self.warning=PatchCollection([],facecolor='none',edgecolor=WARNING_COLOR,linestyles='--',linewidths=.8,zorder=9.4);w.ax.add_collection(self.warning)
            self.problems=PatchCollection([],facecolor='none',edgecolor='#b92d39',alpha=.7,linewidths=.9,zorder=9.7);w.ax.add_collection(self.problems)
            self.constraints=PatchCollection([],facecolor='#dd5790',edgecolor='#bc386d',alpha=.45,linewidths=.7,zorder=10.5);w.ax.add_collection(self.constraints)
            self.problem_points=Line2D([],[],ls='none',marker='o',fillstyle='none',markersize=4,color='#bb354d',alpha=.8,zorder=10.7);w.ax.add_line(self.problem_points)
            self.cuts=LineCollection([],colors=CUT_COLOR,linewidths=2.2,zorder=11.5);w.ax.add_collection(self.cuts)
            self.second_cuts=LineCollection([],colors=SECOND_CUT_COLOR,linewidths=2.2,zorder=11.6);w.ax.add_collection(self.second_cuts)
            self.points=Line2D([],[],ls='none',marker='o',markersize=10,color=CUT_COLOR,markeredgecolor='white',markeredgewidth=.8,zorder=12);w.ax.add_line(self.points)
            self.second_points=Line2D([],[],ls='none',marker='o',markersize=10,color=SECOND_CUT_COLOR,markeredgecolor='white',markeredgewidth=.8,zorder=12);w.ax.add_line(self.second_points)
            self.stamp=None
        data=self.data() if self.active() else None;bank=self.risk_bank() if self.active() else None
        vis=tuple(k for k,v in w.visible_boxes.items() if v.isChecked());order=tuple(w.ordered_layers())
        nominal=bool((w.current_frame or {}).get('nominal_global'));level_text=w.target_sigma.currentText() if hasattr(w,'target_sigma') else '6'
        level=math.inf if level_text=='∞' else float(level_text)
        stamp=(id(data),id(bank),vis,w.view_bounds,order,nominal,level)
        if stamp==self.stamp:self.draw_labels();return
        self.stamp=stamp;paths={};colors={};first=[];second=[];dots=[];far_dots=[];warnings=[];region_groups={};red=[];markers={}
        old_unions=self.union_cache;self.union_cache={};old_cache=self.path_cache;self.path_cache={};self.zone_views={}
        def patch(g):
            previous=old_cache.get(id(g));item=previous if previous is not None and previous[0] is g else (g,w.geometry_patch(g))
            self.path_cache[id(g)]=item;return item[1]
        if not self.active():self.clear_hover()
        current=self.pieces();self.handles=self.handle_geometries(current)
        # A manual pose outside the probability support remains a binary fact.
        inside=True
        if current and not nominal and 'states' in (w.current_frame or {}):
            from .statistics import mean_and_bounds
            p=w.params();states=w.current_frame['states']
            inside=all(np.all(abs(state-mean_and_bounds(p.for_die(i),len(states))[0])<=mean_and_bounds(p.for_die(i),len(states))[1]+1e-12) for i,state in enumerate(states))
        for q in current:
            status=color_for(q,bank,nominal,level,inside);gactive=active_region(q)
            self.zone_views[zone_key(q)]=(q,status,gactive)
            for k,g in q['regions'].items():
                if k=='A':g=gactive
                if g.is_empty or not _in_view(g.bounds,w.view_bounds):continue
                region_groups.setdefault((q['layer'],q['index'],k,status if k=='A' else ''),{})[id(g)]=g
            if status=='red' and not gactive.is_empty and _in_view(gactive.bounds,w.view_bounds):red.append(patch(gactive))
            if status=='pending' and not gactive.is_empty and _in_view(gactive.bounds,w.view_bounds):warnings.append(patch(gactive))
            row=(bank or {}).get('zones',{}).get(zone_key(q))
            geos=[row['markers']] if nominal and row else [v['geometry'] for v in q.get('rule_witnesses',())]
            for g in geos:
                if not g.is_empty and _in_view(g.bounds,w.view_bounds):markers[g.wkb]=g
            for kind,target,dot_list,line_list in (('moved','moved_cut',dots,first),('extra','extra_cut',far_dots,second)):
                face=self.handles.get((self._key(q),kind),q.get(target))
                if face is not None and face.geom_type=='Point':dot_list.append(face.coords[0])
                else:line_list.extend(_lines(face))
        for (layer,index,kind,status),values in sorted(region_groups.items(),key=lambda item:RANK.get(item[0][3],-1)):
            geos=tuple(values.values());cache_key=(layer,index,kind,status,tuple(values));previous=old_unions.get(cache_key)
            g=previous[1] if previous is not None and all(a is b for a,b in zip(previous[0],geos)) else (geos[0] if len(geos)==1 else shapely.union_all(geos))
            self.union_cache[cache_key]=(geos,g);paths.setdefault((layer,kind),[]).append(patch(g));colors.setdefault((layer,kind),[]).append(PALETTE[status] if kind=='A' else COLORS[kind])
        for key in paths:
            if key not in self.artists:
                artist=PatchCollection([],alpha=.66,linewidths=.5);w.ax.add_collection(artist);self.artists[key]=artist
        for (layer,k),artist in self.artists.items():
            shapes=paths.get((layer,k),[]);artist.set_paths(shapes);artist.set_visible(bool(shapes));artist.set_facecolors(colors.get((layer,k),[]));artist.set_edgecolors(colors.get((layer,k),[]))
            artist.set_zorder(2+(order.index(layer) if layer in order else 0)/max(1,len(order))+.001*('BCA'.index(k)+1))
        self.constraints.set_paths([patch(g) for g in markers.values()]);self.constraints.set_visible(bool(markers))
        points=[g.representative_point().coords[0] for g in markers.values()]
        self.problem_points.set_data(np.array(points).T if points else [[],[]]);self.problem_points.set_visible(bool(points))
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
            labels={'red':'当前存在违规','yellow':'当前通过，其他极端存在问题','green':'全部离散极端通过','gray':'失效概率上界满足良率要求','pending':'当前通过，全极端核验中'}
            for key,(q,status,g) in self.zone_views.items():
                if (q['layer'],q['index']) not in selected or status=='green':continue
                if g.is_empty:g=q['source']
                row=(bank or {}).get('zones',{}).get(key,{})
                kinds=q.get('problem_reasons',()) if not nominal else row.get('kinds',q.get('problem_reasons',()))
                if not kinds and status=='yellow':kinds=row.get('kinds',())
                text=f"{q['layer']} #{q['index']+1} · DIE{q['die']+1} · {labels[status]}"
                text+='\n'+('、'.join(kinds) if kinds else q.get('warning') or '核验尚未完成')
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
    def pick(self,x,y,tolerance):
        best=None;pieces=self.pieces();self.handles=self.handle_geometries(pieces)
        for q in pieces:
            if q.get('route_plan') is None:continue
            for kind,g in (('moved',self.handles.get((self._key(q),'moved'))),('extra',self.handles.get((self._key(q),'extra')))):
                for xy in _lines(g):
                    d=_segment_distance(xy,x,y)
                    if best is None or d<best[0]:best=(d,q,xy,kind)
        if best and best[0]<=tolerance:return best[1:]
        for q in self.pieces():
            if q.get('problem_region') is not None and q['problem_region'].covers(shapely.Point(x,y)):return q,None,'problem'
            if q['bridge'].covers(shapely.Point(x,y)):return q,None,'bridge'
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
        self.hover.set_marker('o' if np.linalg.norm(xy[-1]-xy[0])<1e-8 else '');self.hover.set_markersize(11)
        self.hover_key=(self._key(q),kind)
        if not self.pulse_timer.isActive():self.pulse_timer.start()
        return True
    def pulse(self):
        if self.hover is None or not self.hover.get_visible() or not self.active() or self.w._closing:
            self.pulse_timer.stop();return
        wave=.5+.5*math.sin(time.monotonic()*7)
        self.hover.set_alpha(.55+.4*wave);self.hover.set_linewidth(3.+2.*wave)
        self.hover.set_markersize(12.+4.*wave)
        self.w.scene_renderer.draw_feedback(self.hover,(id(self.data()),self.stamp),self.w.legend)
    def clear_hover(self):self.highlight(None)
    def fill_table(self,data):
        if getattr(self.w,'cut_drag',None) and self.w.cut_drag.state is not None:return
        stamp=(id(data),self.active())
        if stamp==self.table_stamp:return
        self.table_stamp=stamp;w=self.w;rows=(data or {}).get('pieces',()) if self.active() else []
        w.reconstruction_table.blockSignals(True);w.reconstruction_table.setRowCount(len(rows))
        for i,q in enumerate(rows):
            status=q.get('warning') or '已按共用动区重构'
            if q['conflict']:status+='；异网间距不足'
            if q.get('rule_violations'):status+='；'+('、'.join(q['rule_violations']))
            values=[f"{q['layer']} #{q['index']+1}"+(f" · 分支{q['branch']}" if q.get('branch_count',1)>1 else ''),f"DIE{q['die']+1}",q['net'],f"{q['movable_area_um2']:.2f}",f"{q['shift_um']:.2f}",
                    '—' if q['gap_um'] is None else f"{q['gap_um']:.2f}",f"{q['bridge_area_um2']:.2f}",f"{q['bridge_span_um']:.2f}",f"{q['bridge_length_um']:.2f}",
                    '—' if q['clearance_um'] is None else f"{q['clearance_um']:.2f}",status]
            for j,value in enumerate(values):w.reconstruction_table.setItem(i,j,W.QTableWidgetItem(str(value)))
        w.reconstruction_table.blockSignals(False)
        w.reconstruction_summary.setText(f'共用动区 {len(rows)} 处。蓝色为运动侧边界，黄色为固定侧边界；分别沿原图追溯路径拖动，跨拐角自动吸附。圆点表示整孔盘；红色填充提示规则或连接问题，可单击查看。')
    def locate(self,row,*_):
        data=self.data()
        if not data or not 0<=row<len(data['pieces']):return
        q=data['pieces'][row];g=shapely.union_all(list(q['regions'].values()))
        if g.is_empty:return
        x0,y0,x1,y1=g.bounds;self.selected=self._key(q)
        self.w.animate_view(((x0+x1)/2,(y0+y1)/2),max(100,(x1-x0)*1.25),max(100,(y1-y0)*1.25))


class CutDrag:
    def __init__(self,window):
        self.w=window;self.state=None;self.line=None;self.latest=None
        self.timer=QtCore.QTimer(window);self.timer.setInterval(33);self.timer.timeout.connect(self._tick)
        for event,fn in [('button_press_event',self.press),('motion_notify_event',self.move),('button_release_event',self.release),('figure_leave_event',self.leave)]:window.canvas.mpl_connect(event,fn)
    def _tolerance(self):
        t=self.w.ax.transData.inverted();return float(np.linalg.norm(t.transform((12,0))-t.transform((0,0))))
    def press(self,event):
        w=self.w
        if not w.reconstruction_toggle.isChecked() or event.inaxes is not w.ax or event.xdata is None or getattr(w.canvas,'die_move_active',False):return
        if getattr(w,'region_select',None) and w.region_select.mode is not None:return
        picked=w.reconstruction_overlay.pick(event.xdata,event.ydata,self._tolerance())
        if picked is None:return
        piece,xy,kind=picked;overlay=w.reconstruction_overlay;overlay.selected=overlay._key(piece);overlay.selected_kind=kind
        if kind in ('bridge','problem'):return
        if event.button!=1:return
        w.stop_animation();w.cancel_previews()
        if event.dblclick:self.clear(overlay._key(piece),piece,kind);return
        if getattr(w,'selection',None):
            w.selection.press=None
            if w.selection.rubber is not None:w.selection.rubber.remove();w.selection.rubber=None
        self.state=dict(piece=piece,kind=kind,start=np.array([event.x,event.y]),moved=False,settings=w.view_settings,
                        frame=w.current_frame,phase=0.,updated=w.view_settings,part=None,
                        handle_offset=(xy.mean(axis=0)-np.asarray(piece['extra_cut' if kind=='extra' else 'moved_cut'].coords[0]) if piece['extra_cut' if kind=='extra' else 'moved_cut'].geom_type=='Point' else np.zeros(2)))
        self.latest=None;self.timer.start();overlay.highlight(picked)
        w.statusBar().showMessage('拖动共用动区边界；另一端保持不动，松开后更新所有工况。')
    def move(self,event):
        if self.state is None:
            if self.w.reconstruction_toggle.isChecked() and event.inaxes is self.w.ax and event.xdata is not None:self.w.reconstruction_overlay.highlight(self.w.reconstruction_overlay.pick(event.xdata,event.ydata,self._tolerance()))
            else:self.w.reconstruction_overlay.clear_hover()
            return
        if event.xdata is None or event.ydata is None:return
        if not self.state['moved'] and np.linalg.norm(np.array([event.x,event.y])-self.state['start'])<2:return
        self.state['moved']=True;self.latest=np.array([event.xdata,event.ydata])
    def _tick(self):
        state=self.state
        if state is None:return
        if self.latest is not None:
            cursor=self.latest-self.state['handle_offset'];self.latest=None;piece=state['piece'];owner=piece['die'];frame=state['frame']
            if state['kind']!='extra':
                from .selection import inverse_geometry
                cursor=np.asarray(inverse_geometry(shapely.Point(cursor),frame['rotation_centers'][owner],frame['states'][owner]).coords[0])
            plan=piece.get('route_plan')
            if plan is not None:
                which=1 if state['kind']=='extra' else 0
                part,clamped=plan.edit_pose(*piece['interval'],which,float(plan.route.project(shapely.Point(cursor))),frame['rotation_centers'][owner],frame['states'][owner],self.w.view_settings.reconstruction_style)
                if part is not None:
                    rows=[r for r in self.w.view_settings.reconstruction_intervals if (r[0],r[1],r[2],normalize_via_refs(r[3]),r[4])!=(*plan.key,plan.identity)]
                    rows.append((*plan.key,plan.identity,part['a'],part['b']))
                    state['updated']=replace(self.w.view_settings,reconstruction_intervals=tuple(rows));state['part']=part
                    line=part['far'] if which else transform(part['near'],frame['rotation_centers'][owner],frame['states'][owner])
                    if self.w.view_settings.reconstruction_live:self._live(part)
                    view_piece=dict(piece,moved_cut=transform(part['near'],frame['rotation_centers'][owner],frame['states'][owner]),extra_cut=part['far'])
                    handles=self.w.reconstruction_overlay.handles if self.w.view_settings.reconstruction_live else self.w.reconstruction_overlay.handle_geometries([view_piece])
                    handle=handles.get((self.w.reconstruction_overlay._key(piece),state['kind']),line)
                    self.w.reconstruction_overlay.highlight((piece,_lines(handle)[0],state['kind']))
                    self.w.render_scene()
    def _live(self,part):
        from .reconstruction_route import warp,failed_connection,missing_area
        state=self.state;old=state['frame'];piece=state['piece'];plan=piece['route_plan']
        from .reconstruction_angles import solve
        if self.w.view_settings.reconstruction_boundary_mode=='perpendicular':regions,error=warp(part,plan,old['rotation_centers'][piece['die']],old['states'][piece['die']],self.w.view_settings.reconstruction_style)
        else:part,regions,error,_=solve(self.w.engine.reconstructor,plan,part,old['rotation_centers'][piece['die']],old['states'][piece['die']],quick=True)
        if error:
            self.w.statusBar().showMessage(error+'；已取消连接段');regions=failed_connection(part,plan,old['rotation_centers'][piece['die']],old['states'][piece['die']])
        moved_cut=transform(part['near'],old['rotation_centers'][piece['die']],old['states'][piece['die']])
        replacement=dict(piece,regions=regions,nominal_regions={k:part[k] for k in 'ABC'},moved=regions['C'],movable=part['C'],fixed=regions['B'],bridge=regions['A'],
                         fallback_regions=failed_connection(part,plan,old['rotation_centers'][piece['die']],old['states'][piece['die']]),connection_failed=bool(error),
                         missing_region=missing_area(part,plan,old['rotation_centers'][piece['die']],old['states'][piece['die']]),
                         cut=part['near'],moved_cut=moved_cut,extra_cut=part['far'],span=part['A'],interval=(part['a'],part['b']),boundary_angles=part.get('angles',(0.,0.)),valid=not bool(error),warning=error or '',whole_landing=part['a']==0,whole_fixed_landing=part['far'].geom_type=='Point',rule_violations=())
        data=old['reconstruction'];items=[replacement if q is piece else dict(q,regions=dict(q['regions'])) for q in data['pieces']]
        replacements={k:[dict(v) for v in values] for k,values in data['replacements'].items()}
        feature=[q for q in items if (q['layer'],q['index'])==(piece['layer'],piece['index'])]
        from .reconstruction import compose_feature
        from .reconstruction import feature_moves
        copper,_=compose_feature(piece.get('source',plan.source),feature,plan.tolerance,feature_moves(feature,old['states'],self.w.view_settings.reconstruction_style))
        for q in replacements.get(piece['layer'],()):
            if piece['index'] in q['indices']:q['geometry']=copper
        self.w.current_frame=dict(old,reconstruction=dict(data,configuration=state['updated'].reconstruction_key(),pieces=items,replacements=replacements,violations=[],constraints_complete=False))
        from .reconstruction import decorate_problems
        decorate_problems(self.w.current_frame['reconstruction'])
        self.w.constraint_feedback.request(self.w.current_frame,state['updated'])
        self.w.reconstruction_yield.refresh(state['updated'])
        self.w.reconstruction_overlay.stamp=None;self.w.reconstruction_overlay.draw()
    def release(self,event):
        if self.state is None:return
        if event.xdata is not None and event.ydata is not None and self.state['moved']:self.latest=np.array([event.xdata,event.ydata])
        self._tick();state=self.state
        if state.get('part') is not None:self._live(state['part'])
        self.state=None;self.latest=None;self.timer.stop()
        self.w.reconstruction_overlay.clear_hover()
        if not state['moved']:return
        self.w.view_settings=state['updated']
        self.w.invalidate_reconstruction_edit();self.w.request_preview(quiet=True)
    def cancel(self):
        if self.state is None:return False
        original=self.state['frame'];self.state=None;self.latest=None;self.timer.stop()
        self.w.constraint_feedback.cancel();self.w.reconstruction_yield.refresh()
        self.w.current_frame=original;self.w.reconstruction_overlay.clear_hover()
        self.w.reconstruction_overlay.stamp=None;self.w.reconstruction_overlay.draw();self.w.render_scene()
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
    """Box-select a 动区 (movable) or 静区 (stays put) on the reconstruction page.

    The rectangles are stored per fixed-RDL layer in
    ``view_settings.reconstruction_regions`` and applied after the automatic
    split, so the software's guess stays visible wherever the user has not
    overridden it. Commit re-runs the preview quietly, like the cut drag.
    """

    def __init__(self, window):
        self.w = window
        self.mode = None
        self.state = None
        self.rect = None
        for name, fn in [('button_press_event', self.press),
                         ('motion_notify_event', self.move),
                         ('button_release_event', self.release)]:
            window.canvas.mpl_connect(name, fn)

    def set_mode(self, mode):
        """'move', 'stay' or None (off)."""
        self.mode = mode
        if mode is None:
            self._drop()
            self.w.statusBar().showMessage('已退出手动区域框选')

    def _drop(self):
        if self.rect is not None:
            try:
                self.rect.remove()
            except Exception:
                pass
            self.rect = None
        self.state = None

    def _point(self, event):
        if event.xdata is None or event.ydata is None:
            return None
        return float(event.xdata), float(event.ydata)

    def press(self, event):
        if self.mode is None or event.button != 1 or event.inaxes is not self.w.ax:
            return
        point = self._point(event)
        if point is None:
            return
        if not self.w.reconstruction_toggle.isChecked():return
        # The canvas rubber band and the cut drag listen to the same press.
        selection = getattr(self.w, 'selection', None)
        if selection is not None:
            selection.press = None
            if getattr(selection, 'rubber', None) is not None:
                try:
                    selection.rubber.remove()
                except Exception:
                    pass
                selection.rubber = None
        self.state = {'start': point, 'current': point}
        colour = REGION_COLORS.get(self.mode, '#555555')
        self.rect = Rectangle(point, 0., 0., facecolor=colour, alpha=.12,
                              edgecolor=colour, lw=1.5, linestyle=(0, (5, 3)),
                              zorder=12.2)
        self.w.ax.add_patch(self.rect)
        self.w.statusBar().showMessage(f'框选{REGION_LABELS[self.mode]}：松开鼠标即写入')
        return True

    def move(self, event):
        if self.state is None or self.rect is None or event.button != 1:
            return
        point = self._point(event)
        if point is None:
            return
        self.state['current'] = point
        self.w.canvas.draw_idle()
        x0, y0 = self.state['start']
        x1, y1 = point
        self.rect.set_xy((min(x0, x1), min(y0, y1)))
        self.rect.set_width(abs(x1 - x0))
        self.rect.set_height(abs(y1 - y0))
        return True

    def release(self, event):
        if self.state is None:
            return
        state = self.state
        self.state = None
        if self.rect is not None:
            try:
                self.rect.remove()
            except Exception:
                pass
            self.rect = None
        self.w.canvas.draw_idle()
        if event.inaxes is not self.w.ax or self._point(event) is None:return True
        state['current']=self._point(event)
        x0, y0 = state['start']
        x1, y1 = state['current']
        if abs(x1 - x0) <= 0. or abs(y1 - y0) <= 0.:
            self.w.statusBar().showMessage('框选太小，没有写入')
            return True
        layers = self._layers()
        if not layers:
            self.w.statusBar().showMessage('当前没有可划分的固定RDL，先勾选“重构预演”')
            return True
        bounds = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
        from dataclasses import replace
        regions = list(getattr(self.w.view_settings, 'reconstruction_regions', ()) or ())
        for layer in layers:
            regions.append((layer, bounds, self.mode))
        self.w.view_settings = replace(self.w.view_settings, reconstruction_regions=tuple(regions))
        self.w.dirty=True
        self.w.statusBar().showMessage(
            f'已写入 {len(layers)} 个图层的{REGION_LABELS[self.mode]}；可继续框选，或点“清除全部手动区域”恢复')
        self.w.request_preview(quiet=True)
        return True

    def _layers(self):
        try:
            data = self.w.reconstruction_overlay.data()
        except Exception:
            data = None
        layers = sorted({q['layer'] for q in (data or {}).get('pieces', [])
                         if q['layer'] in self.w.visible_boxes and self.w.visible_boxes[q['layer']].isChecked()})
        if layers:
            return layers
        return []
