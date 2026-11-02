"""Draw retained barriers, failed gate rectangles and affected source outlines."""
import numpy as np
from matplotlib.collections import PatchCollection
from shapely.geometry import Point
from shapely.strtree import STRtree
from .qt_compat import QtWidgets as W, QtGui, QtCore, DISPLAY, TOOLTIP, BACKGROUND, HORIZONTAL
from .width_analysis import COLORS, ADEQUATE, NARROW, BLOCKED, gate_rectangle
from .viewport import overlaps

HEADERS = ['状态','固定 RDL','通道两侧','原可通','必须通过','现可通','原间距 / μm','现间距 / μm','保宽需 / μm','最小需 / μm','问题线路','位置 / μm']
INTRO = '按固定 RDL 原始线路穿越数比较各姿态的孔盘通道容量。勾选后显示孔盘、固定障碍和风险线路。'


class ChannelRows(QtCore.QAbstractTableModel):
    """Virtual cells keep a full-package 'show adequate' query responsive."""
    def __init__(self,parent):
        super().__init__(parent)
        self.rows = []

    def replace_rows(self,rows):
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def rowCount(self,parent=QtCore.QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self,parent=QtCore.QModelIndex()):
        return 0 if parent.isValid() else len(HEADERS)

    def headerData(self,section,orientation,role=DISPLAY):
        if role==DISPLAY and orientation==HORIZONTAL:
            return HEADERS[section]
        return super().headerData(section,orientation,role)

    def data(self,index,role=DISPLAY):
        if not index.isValid():
            return None
        r = self.rows[index.row()]
        if role==TOOLTIP:
            return WidthOverlay.description(r)
        if role==BACKGROUND:
            color = QtGui.QColor(COLORS[r['status']])
            color.setAlpha(48)
            return color
        if role==DISPLAY:
            return (r['status'],r['plane'],' ↔ '.join(r['pair']),r['original'],r['required'],r['current'],
                    f"{r['nominal_gap_um']:.2f}",f"{r['gap_um']:.2f}",f"{r['keep_um']:.2f}",f"{r['minimum_um']:.2f}",
                    '、'.join(f'{k} #{j+1}' for k,j in r['refs']),f"{r['point'][0]:.1f}, {r['point'][1]:.1f}")[index.column()]
        return None


class ChannelTable(W.QTableView):
    def __init__(self):
        super().__init__()
        self.setModel(ChannelRows(self))
        self.setSelectionBehavior(getattr(W.QAbstractItemView,'SelectionBehavior',W.QAbstractItemView).SelectRows)
        self.verticalHeader().hide()
        self.setColumnWidth(2,265)


class WidthOverlay:
    def __init__(self, window):
        self.w = window
        self.barriers = self.boxes = self.outlines = None
        self.labels = []
        self.rows = []
        self.tree = None
        self.selected = None
        self.stamp = self.table_stamp = None
        window.canvas.mpl_connect('motion_notify_event',self.hover)

    def draw(self):
        w = self.w
        data = (w.current_frame or {}).get('width_analysis') if w.width_toggle.isChecked() else None
        if self.boxes is None or self.boxes.axes is not w.ax or self.boxes not in w.ax.collections:
            self.labels = []
            self.barriers = PatchCollection([],facecolor='#c3d2d8',edgecolor='#657d8b',linewidths=.6,zorder=4)
            self.boxes = PatchCollection([],linewidths=1.3,zorder=11.9)
            self.outlines = PatchCollection([],facecolors='none',linewidths=1.55,zorder=12.1)
            for item in (self.barriers,self.boxes,self.outlines):
                w.ax.add_collection(item)
            self.stamp = self.table_stamp = None
        visible = frozenset(k for k,check in w.visible_boxes.items() if check.isChecked())
        flags = (id(data),visible,w.width_sufficient_toggle.isChecked())
        stamp = (flags,w.view_bounds,self.selected,id(w.current_frame),w.reconstruction_toggle.isChecked(),w.deformation_toggle.isChecked())
        if self.stamp == stamp and (getattr(w,'_in_animation',False) or self.table_stamp==flags):
            return
        self.stamp = stamp
        active = [r for r in (data or {}).get('rows',[]) if (w.width_sufficient_toggle.isChecked() or r['status']!=ADEQUATE) and visible.intersection(r['layers'])
                  and all(not ks or visible.intersection(ks) for ks in r['boundary_layers'])
                  and all(any(k in visible for k,_ in refs) for refs in r['boundary_refs'])
                  and any(k in visible for k,_ in r['refs'])]
        rules = (data or {}).get('parameters',{})
        if getattr(self,'geometry_data',None) is not data:
            self.geometry_data = data
            self.rectangles = {}
        def rect(r):
            key = id(r)
            if key not in self.rectangles:
                self.rectangles[key]=gate_rectangle(r,rules['width_um'],rules['space_um'])
            return self.rectangles[key]
        shown, rectangles = [], []
        for r in active:
            x,y = r['point']
            radius = max(r['gap_um']/2,rules['width_um'])+(rules['width_um']+rules['space_um'])*.65
            if overlaps((x-radius,y-radius,x+radius,y+radius),w.view_bounds):
                rectangle = rect(r)
                if overlaps(rectangle.bounds,w.view_bounds):
                    shown.append(r)
                    rectangles.append(rectangle)
        objects = [o['geometry'] for o in (data or {}).get('retained',[]) if visible.intersection(o['layers'])
                   and any(k in visible for k,_ in o['refs']) and overlaps(o['geometry'].bounds,w.view_bounds)]
        self.barriers.set_paths([w.geometry_patch(g) for g in objects])
        self.barriers.set_visible(bool(objects))
        self.boxes.set_paths([w.geometry_patch(g) for g in rectangles])
        colors = [COLORS[r['status']] for r in shown]
        from matplotlib.colors import to_rgba
        self.boxes.set_facecolors([to_rgba(c,.14) for c in colors])
        self.boxes.set_edgecolors(colors)
        self.boxes.set_visible(bool(shown))
        # Use the displayed replacement geometry when a source wire was deformed.
        changed = {}
        frame = w.current_frame or {}
        for toggle,feature in ((w.deformation_toggle,'deformation'),(w.reconstruction_toggle,'reconstruction')):
            if toggle.isChecked():
                for k,items in frame.get(feature,{}).get('replacements',{}).items():
                    for q in items:
                        changed.update({(k,i):q['geometry'] for i in q['indices']})
        refs = dict.fromkeys(ref for r in active if r['status']!=ADEQUATE for ref in r['refs'] if ref[0] in visible)
        paths, inks = [], []
        danger = {ref for r in active if r['status']==BLOCKED for ref in r['refs']}
        for k,i in refs:
            g = changed.get((k,i),w.geometry_cache.layers[k]['geometry'][i])
            if overlaps(g.bounds,w.view_bounds):
                paths.append(w.geometry_patch(g))
                inks.append(COLORS[BLOCKED if (k,i) in danger else NARROW])
        self.outlines.set_paths(paths)
        self.outlines.set_edgecolors(inks)
        self.outlines.set_visible(bool(paths))
        self.rows = shown
        self.tree = STRtree(rectangles) if shown else None
        # Every rectangle stays visible. Thin labels by screen-space overlap only.
        ordered = sorted(shown,key=lambda r:self.identity(r)!=self.selected)
        positions = []
        occupied = set()
        for r in ordered:
            px = w.ax.transData.transform(r['point'])
            cell = (int(px[0]//155),int(px[1]//40))
            if cell in occupied:
                continue
            occupied.add(cell)
            pos = len(positions)
            positions.append(px)
            if pos==len(self.labels):
                a = w.ax.annotate('',xy=(0,0),xytext=(10,12),textcoords='offset points',fontsize=7,
                                  zorder=12.6,bbox=dict(facecolor='white',alpha=.9,pad=1.5),
                                  arrowprops=dict(arrowstyle='->',lw=.9))
                self.labels.append(a)
            a = self.labels[pos]
            a.xy = tuple(r['point'])
            a.set_text(f"原可通 {r['original']}　必须 {r['required']}　现可通 {r['current']}")
            a.set_color(COLORS[r['status']])
            a.arrow_patch.set_color(COLORS[r['status']])
            a.get_bbox_patch().set_edgecolor(COLORS[r['status']])
            a.set_visible(True)
        for a in self.labels[len(positions):]:
            a.set_visible(False)
        if not getattr(w,'_in_animation',False) and self.table_stamp!=flags:
            self.table_stamp = flags
            self.fill_table(active,data)

    @staticmethod
    def identity(r):
        return (r['plane'],r['pair'],tuple(r['point']))

    @staticmethod
    def description(r):
        note = '　新障碍分段，需核对绕行侧别。' if r['split'] else ''
        if r['unresolved']:
            note += '　存在未确定的芯片归属。'
        return (f"{' ↔ '.join(r['pair'])}　{r['status']}\n原可通 {r['original']} 根，必须通过 {r['required']} 根，"
                f"现可通 {r['current']} 根。原线宽 {', '.join(f'{v:.2f}' for v in r['widths_um'])} μm。"
                f"各空隙净宽 {', '.join(f'{v:.2f}' for v in r['slots_um']) or '0'} μm。{note}")

    def fill_table(self,rows,data):
        w = self.w
        w.width_rows = rows
        w.width_table.model().replace_rows(rows)
        if not data:
            w.width_summary.setText(INTRO)
        elif not data['examined']:
            w.width_summary.setText('当前固定 RDL 没有识别到有名义线路穿越的通道。请核对图层用途、层序和连接。')
        else:
            q = data['counts']
            w.width_summary.setText(f"检查 {data['examined']} 个名义穿越截面。宽度足够 {q[ADEQUATE]} 个，"
                                    f"需局部收窄 {q[NARROW]} 个，宽度不足 {q[BLOCKED]} 个。当前显示 {len(rows)} 行。")

    def hover(self,event):
        w = self.w
        if self.tree is None or event.inaxes is not w.ax or event.xdata is None:
            if w.canvas.toolTip():
                w.canvas.setToolTip('')
            return
        hits = self.tree.query(Point(event.xdata,event.ydata),predicate='intersects')
        text = self.description(self.rows[int(hits[0])]) if len(hits) else ''
        if text!=w.canvas.toolTip():
            w.canvas.setToolTip(text)

    def locate(self,row):
        if 0<=row<len(getattr(self.w,'width_rows',[])):
            r = self.w.width_rows[row]
            self.selected = self.identity(r)
            rules=self.w.current_frame['width_analysis']['parameters']
            x0,y0,x1,y1 = gate_rectangle(r,rules['width_um'],rules['space_um']).bounds
            span = max(100.,(x1-x0)*2.5,(y1-y0)*2.5)
            self.w.animate_view(r['point'],span,span)
