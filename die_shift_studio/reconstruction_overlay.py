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

REGION_COLORS={'move':'#2f7bd6','stay':'#c2571f'}
REGION_LABELS={'move':'动区','stay':'静区'}
COLORS={'A':'#edbb77','B':'#b6c8dd','C':'#a5c7bd'}
CUT_COLOR='#237ab9'
SECOND_CUT_COLOR='#ba637c'
WARNING_COLOR='#b570b8'
DRAG_FIRST_CUT=True


def _lines(g):
    if g is None or g.is_empty:return []
    if g.geom_type=='Point':return [np.repeat(shapely.get_coordinates(g),2,axis=0)]
    if g.geom_type=='LineString':return [np.asarray(g.coords)]
    return [xy for q in shapely.get_parts(g) if q is not g for xy in _lines(q)]


def _in_view(bounds,view):
    return bounds[0]<=view[2] and bounds[2]>=view[0] and bounds[1]<=view[3] and bounds[3]>=view[1]


def _segment_distance(points,x,y):
    a=points[:-1];v=np.diff(points,axis=0);norm=np.sum(v*v,axis=1)
    t=np.clip(np.sum((np.array([x,y])-a)*v,axis=1)/np.maximum(norm,1e-20),0,1)
    return float(np.min(np.linalg.norm(a+t[:,None]*v-[x,y],axis=1)))


class ReconstructionOverlay:
    def __init__(self,window):
        self.w=window;self.artists={};self.stamp=self.table_stamp=None
        self.selected=self.selected_kind=None;self.hover=None;self.hover_key=None
        self.end_artists=[];self.region_artists=[];self.arrow_artists=[]
    def active(self):return self.w.reconstruction_toggle.isChecked()
    @staticmethod
    def _key(q):return q['layer'],int(q['index']),normalize_via_refs(q.get('via_refs',q['vias']))
    def data(self):return (self.w.current_frame or {}).get('reconstruction')
    def pieces(self):return [q for q in (self.data() or {}).get('pieces',()) if self.active() and self.w.visible_boxes[q['layer']].isChecked()]
    def piece_for(self,key):return next((q for q in self.pieces() if self._key(q)==key),None)
    def draw(self):
        w=self.w
        if not self.artists or next(iter(self.artists.values())).axes is not w.ax:
            self.artists={}
            for k in 'BCA':
                self.artists[k]=PatchCollection([],facecolor=COLORS[k],edgecolor=COLORS[k],alpha=.66,linewidths=.5,zorder=9+('BCA'.index(k))*.1)
                w.ax.add_collection(self.artists[k])
            self.warning=PatchCollection([],facecolor='none',edgecolor=WARNING_COLOR,linestyles='--',linewidths=1.1,zorder=9.4);w.ax.add_collection(self.warning)
            self.cuts=LineCollection([],colors=CUT_COLOR,linewidths=2.2,zorder=11.5);w.ax.add_collection(self.cuts)
            self.second_cuts=LineCollection([],colors=SECOND_CUT_COLOR,linewidths=2.2,zorder=11.6);w.ax.add_collection(self.second_cuts)
            self.points=Line2D([],[],ls='none',marker='o',markersize=10,color=CUT_COLOR,markeredgecolor='white',markeredgewidth=.8,zorder=12);w.ax.add_line(self.points)
            self.stamp=None
        data=self.data() if self.active() else None
        vis=tuple(k for k,v in w.visible_boxes.items() if v.isChecked())
        stamp=(id(data),vis,w.view_bounds)
        if stamp==self.stamp:return
        self.stamp=stamp;paths={k:[] for k in 'ABC'};first=[];second=[];dots=[];warnings=[];seen=set()
        for q in self.pieces():
            if not _in_view(q['route_plan'].source.bounds if q.get('route_plan') else q['fixed'].bounds,w.view_bounds) and not any(not g.is_empty and _in_view(g.bounds,w.view_bounds) for g in q['regions'].values()):continue
            for k,g in q['regions'].items():
                if g.is_empty:continue
                ident=(q['layer'],q['index'],k)
                if k=='B' and ident in seen:continue
                seen.add(ident);paths[k].append(w.geometry_patch(g))
            if q.get('warning'):
                g=shapely.union_all(list(q['regions'].values()))
                if not g.is_empty:warnings.append(w.geometry_patch(g))
            near=q.get('moved_cut')
            if near is not None and near.geom_type=='Point':dots.append(near.coords[0])
            else:first.extend(_lines(near))
            second.extend(_lines(q.get('extra_cut')))
        for k,artist in self.artists.items():artist.set_paths(paths[k]);artist.set_visible(bool(paths[k]))
        self.warning.set_paths(warnings);self.warning.set_visible(bool(warnings))
        self.cuts.set_segments(first);self.cuts.set_visible(bool(first))
        self.second_cuts.set_segments(second);self.second_cuts.set_visible(bool(second))
        self.points.set_data(np.array(dots).T if dots else [[],[]]);self.points.set_visible(bool(dots))
    def pick(self,x,y,tolerance):
        best=None
        for q in self.pieces():
            if q.get('route_plan') is None:continue
            for kind,g in (('moved',q.get('moved_cut')),('extra',q.get('extra_cut'))):
                for xy in _lines(g):
                    d=_segment_distance(xy,x,y)
                    if best is None or d<best[0]:best=(d,q,xy,kind)
        if best and best[0]<=tolerance:return best[1:]
        for q in self.pieces():
            if q['bridge'].covers(shapely.Point(x,y)):return q,None,'bridge'
        return None
    def highlight(self,picked):
        if self.hover is None or self.hover.axes is not self.w.ax:
            self.hover=Line2D([],[],color=CUT_COLOR,lw=4,alpha=.7,zorder=13);self.w.ax.add_line(self.hover)
        if picked is None or picked[2]=='bridge':self.hover.set_visible(False);return False
        q,xy,kind=picked;self.hover.set_data(xy.T);self.hover.set_visible(True)
        self.hover.set_color(SECOND_CUT_COLOR if kind=='extra' else CUT_COLOR)
        self.hover.set_marker('o' if np.linalg.norm(xy[-1]-xy[0])<1e-8 else '');self.hover.set_markersize(11)
        self.w.canvas.draw_idle();return True
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
            values=[f"{q['layer']} #{q['index']+1}",f"DIE{q['die']+1}",q['net'],f"{q['movable_area_um2']:.2f}",f"{q['shift_um']:.2f}",
                    '—' if q['gap_um'] is None else f"{q['gap_um']:.2f}",f"{q['bridge_area_um2']:.2f}",f"{q['bridge_span_um']:.2f}",f"{q['bridge_length_um']:.2f}",
                    '—' if q['clearance_um'] is None else f"{q['clearance_um']:.2f}",status]
            for j,value in enumerate(values):w.reconstruction_table.setItem(i,j,W.QTableWidgetItem(str(value)))
        w.reconstruction_table.blockSignals(False)
        w.reconstruction_summary.setText(f'共用动区 {len(rows)} 处。蓝色为运动侧边界，玫红为固定侧边界；分别沿原图追溯路径拖动，跨拐角自动吸附。紫色轮廓提示无法满足的区域。')
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
        if kind=='bridge':return
        if event.button!=1:return
        w.stop_animation();w.cancel_previews()
        if event.dblclick:self.clear(overlay._key(piece),piece,kind);return
        if getattr(w,'selection',None):
            w.selection.press=None
            if w.selection.rubber is not None:w.selection.rubber.remove();w.selection.rubber=None
        self.state=dict(piece=piece,kind=kind,start=np.array([event.x,event.y]),moved=False,settings=w.view_settings,
                        frame=w.current_frame,phase=0.,updated=w.view_settings,part=None)
        self.latest=None;self.timer.start();overlay.highlight(picked)
        w.statusBar().showMessage('拖动共用动区边界；另一端保持不动，松开后更新所有工况。')
    def move(self,event):
        if self.state is None:
            if self.w.reconstruction_toggle.isChecked() and event.inaxes is self.w.ax and event.xdata is not None:self.w.reconstruction_overlay.highlight(self.w.reconstruction_overlay.pick(event.xdata,event.ydata,self._tolerance()))
            return
        if event.xdata is None or event.ydata is None:return
        if not self.state['moved'] and np.linalg.norm(np.array([event.x,event.y])-self.state['start'])<2:return
        self.state['moved']=True;self.latest=np.array([event.xdata,event.ydata])
    def _tick(self):
        state=self.state
        if state is None:return
        hover=self.w.reconstruction_overlay.hover
        state['phase']+=.35
        if hover is not None:hover.set_alpha(.55+.4*(.5+.5*math.sin(state['phase'])));hover.set_linewidth(3.5+1.5*math.sin(state['phase'])**2)
        if self.latest is not None:
            cursor=self.latest;self.latest=None;piece=state['piece'];owner=piece['die'];frame=state['frame']
            if state['kind']!='extra':
                from .selection import inverse_geometry
                cursor=np.asarray(inverse_geometry(shapely.Point(cursor),frame['rotation_centers'][owner],frame['states'][owner]).coords[0])
            engine=self.w.engine;reconstructor=getattr(engine,'reconstructor',None)
            if reconstructor is not None:
                updated,part=reconstructor.edited(piece,1 if state['kind']=='extra' else 0,cursor)
                state['updated']=replace(self.w.view_settings,reconstruction_intervals=updated.reconstruction_intervals);state['part']=part
                if part is not None:
                    line=part['far'] if state['kind']=='extra' else transform(part['near'],frame['rotation_centers'][owner],frame['states'][owner])
                    self.w.reconstruction_overlay.highlight((piece,_lines(line)[0],state['kind']))
                    if self.w.view_settings.reconstruction_live:self._live(part)
        self.w.canvas.draw_idle()
    def _live(self,part):
        from .reconstruction_route import warp
        state=self.state;old=state['frame'];piece=state['piece'];plan=piece['route_plan']
        regions,error=warp(part,plan,old['rotation_centers'][piece['die']],old['states'][piece['die']])
        if error:
            self.w.statusBar().showMessage(error);return
        moved_cut=transform(part['near'],old['rotation_centers'][piece['die']],old['states'][piece['die']])
        replacement=dict(piece,regions=regions,nominal_regions={k:part[k] for k in 'ABC'},moved=regions['C'],movable=part['C'],fixed=regions['B'],bridge=regions['A'],
                         cut=part['near'],moved_cut=moved_cut,extra_cut=part['far'],span=part['A'],interval=(part['a'],part['b']))
        data=old['reconstruction'];items=[replacement if q is piece else q for q in data['pieces']]
        self.w.current_frame=dict(old,reconstruction=dict(data,pieces=items))
        self.w.reconstruction_overlay.stamp=None;self.w.reconstruction_overlay.draw()
    def release(self,event):
        if self.state is None:return
        if event.xdata is not None and event.ydata is not None and self.state['moved']:self.latest=np.array([event.xdata,event.ydata])
        self._tick();state=self.state;self.state=None;self.latest=None;self.timer.stop()
        self.w.reconstruction_overlay.clear_hover()
        if not state['moved']:return
        self.w.view_settings=state['updated'];self.w.current_frame=state['frame']
        self.w.invalidate_reconstruction_edit();self.w.request_preview(quiet=True)
    def leave(self,event=None):
        if self.state is None:self.w.reconstruction_overlay.clear_hover()
    def clear(self,key,piece,kind='cut'):
        which=1 if kind=='extra' else 0;plan=piece.get('route_plan')
        if plan is None:return
        a,b=piece['interval'];target=plan.nominal_second if which else plan.nominal_first
        settings,_=self.w.engine.reconstructor.edited(piece,which,plan.route.interpolate(target).coords[0])
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
    rows=[r for r in settings.reconstruction_intervals if (r[0],r[1],r[2],normalize_via_refs(r[3]))!=plan.key]
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
