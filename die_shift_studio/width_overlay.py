"""Lightweight viewport rendering and inspection for channel-capacity results."""
import numpy as np
from matplotlib.collections import PatchCollection,LineCollection
from shapely.geometry import Point
from shapely.strtree import STRtree
from .qt_compat import QtWidgets as W,QtGui
from .width_analysis import COLORS,RANK
from .viewport import overlaps


class WidthOverlay:
    def __init__(self,window):
        self.w=window;self.boxes=None;self.edges=None;self.motion=None;self.stamp=None;self.table_stamp=None;self.rows=[];self.tree=None
        window.canvas.mpl_connect('motion_notify_event',self.hover)
    def visible(self,row):
        w=self.w
        return any(w.visible_boxes[k].isChecked() for k in row['layers']) and all(w.visible_boxes.get(k) is not None and w.visible_boxes[k].isChecked() for k,_ in row['boundary_refs'])
    def draw(self):
        w=self.w;frame=w.current_frame or {};data=frame.get('width_analysis') if w.width_toggle.isChecked() else None
        if self.boxes is None or self.boxes not in w.ax.collections:
            self.boxes=PatchCollection([],linewidths=.9,zorder=7.1);self.edges=PatchCollection([],facecolors='none',linewidths=1.7,zorder=7.7)
            self.motion=LineCollection([],colors='#bf862c',linestyles=(0,(3,2)),linewidths=1.3,zorder=12.2)
            for artist in (self.boxes,self.edges,self.motion):w.ax.add_collection(artist)
            self.stamp=None
        # Unresolved motion is always explicit, independent of width analysis.
        warnings=[]
        for issue in frame.get('motion_issues',[]):
            key=issue['layer'];index=issue['index']-1
            if w.visible_boxes[key].isChecked():
                p=w.data.layers[key][index];warnings.append(np.vstack((p,p[0])))
        self.motion.set_segments(warnings);self.motion.set_visible(bool(warnings))
        stamp=(id(data),tuple(k for k,b in w.visible_boxes.items() if b.isChecked()),w.view_bounds)
        if stamp!=self.stamp:
            self.stamp=stamp;active=[] if data is None else [q for q in data['rows'] if q['draw'] and self.visible(q)]
            # Remove only nearby duplicate paint, not any numerical audit row.
            visible=[]
            for q in active:
                if not q['draw'] or not overlaps(q['rectangle'].bounds,w.view_bounds):continue
                if any(q['plane']==p['plane'] and q['refs']==p['refs'] and np.linalg.norm(np.asarray(q['point'])-p['point'])<2*max(q['width_um'],q['space_um']) for p in visible[-128:]):continue
                visible.append(q)
            faces=[]
            for q in visible:
                rgba=QtGui.QColor(COLORS[q['status']]);faces.append((rgba.redF(),rgba.greenF(),rgba.blueF(),.16 if q['status']=='充足' else .25))
            self.boxes.set_paths([w.geometry_patch(q['rectangle']) for q in visible]);self.boxes.set_facecolors(faces);self.boxes.set_edgecolors([COLORS[q['status']] for q in visible]);self.boxes.set_visible(bool(visible))
            affected={}
            for q in active:
                if q['status']=='充足':continue
                for ref in q['refs']:
                    old=affected.get(ref)
                    if old is None or RANK[q['status']]>RANK[old]:affected[ref]=q['status']
            patches=[];colors=[]
            for q in (data or {}).get('highlights',[]):
                statuses=[affected[ref] for ref in q['refs'] if ref in affected and w.visible_boxes[ref[0]].isChecked()]
                if not statuses or not overlaps(q['geometry'].bounds,w.view_bounds):continue
                patches.append(w.geometry_patch(q['geometry']));colors.append(COLORS[max(statuses,key=RANK.get)])
            self.edges.set_paths(patches);self.edges.set_edgecolors(colors);self.edges.set_visible(bool(patches))
            self.rows=visible;self.tree=STRtree([q['rectangle'] for q in visible]) if visible else None
            self.active_rows=active
        if not getattr(w,'_in_animation',False) and (id(data),stamp[1])!=self.table_stamp:
            self.table_stamp=(id(data),stamp[1]);self.fill_table(data)
    def fill_table(self,data):
        w=self.w;rows=getattr(self,'active_rows',[]) if data else [];w.width_rows=rows
        t=w.width_table;t.blockSignals(True);t.setRowCount(len(rows))
        for i,q in enumerate(rows):
            margin='分段通道' if q['margin_um'] is None else f"{q['margin_um']:.3f}"
            values=[q['status'],q['plane'],f"{q['gap_um']:.3f}",q['strands'],q['capacity'],f"{q['required_um']:.3f}",margin,' / '.join(q['ends'])]
            for j,v in enumerate(values):
                item=W.QTableWidgetItem(str(v));color=QtGui.QColor(COLORS[q['status']]);color.setAlpha(60);item.setBackground(color);item.setToolTip(self.description(q));t.setItem(i,j,item)
        t.blockSignals(False);t.resizeColumnsToContents();t.horizontalHeader().setStretchLastSection(True)
        counts=(data or {}).get('counts',{});suffix='；'.join(f'{k}{v}' for k,v in counts.items() if v)
        w.width_summary.setText((f"已检查{len(data['rows'])}处截面，当前显示{len(rows)}处窄口："+suffix+'。绿色：有额外容量；黄色：无额外线位；红色：容量不足；灰色：待确认。' if data else '勾选“宽度分析”后计算当前姿态；使用实际连通关系及当前线宽、线距。')+' 保持当前通道分配的必要条件，不是整图可布通证明。')
    @staticmethod
    def description(q):
        s=f"{q['plane']}层 · {q['status']}\n可用 {q['gap_um']:.3f} μm；需求 {q['strands']} 条；可容 {q['capacity']} 条\nW = {q['strands']} × {q['width_um']:g} + {q['strands']+1} × {q['space_um']:g} = {q['required_um']:.3f} μm\n{q['detail']}"
        if 'available_slots_um' in q:s+='\n分段净宽 / μm：'+', '.join(f'{v:.3f}' for v in q['available_slots_um'])
        if q['mixed_rules']:s+='\n同一物理层存在两套规则，按较大的线宽和线距保守计算。'
        return s
    def hover(self,event):
        w=self.w
        if self.tree is None or event.inaxes is not w.ax or event.xdata is None:
            if w.canvas.toolTip():w.canvas.setToolTip('')
            return
        if getattr(getattr(w,'mouse_die',None),'drag',None) is not None:return
        hits=self.tree.query(Point(event.xdata,event.ydata),predicate='intersects')
        text=self.description(self.rows[int(hits[0])]) if len(hits) else ''
        if w.canvas.toolTip()!=text:w.canvas.setToolTip(text)
    def locate(self,row):
        rows=getattr(self.w,'width_rows',[])
        if not 0<=row<len(rows):return
        q=rows[row];x0,y0,x1,y1=q['rectangle'].bounds
        self.w.animate_view(q['point'],max(100,(x1-x0)*1.7),max(100,(y1-y0)*1.7))
