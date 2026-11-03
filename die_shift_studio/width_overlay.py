"""Draw retained barriers, failed gate rectangles and affected source outlines."""
import numpy as np
from matplotlib.colors import to_rgb
from matplotlib.collections import PatchCollection
from shapely.geometry import Point
from shapely.strtree import STRtree
from .qt_compat import QtWidgets as W, QtGui, QtCore, DISPLAY, TOOLTIP, BACKGROUND, HORIZONTAL
from .width_analysis import COLORS, ADEQUATE, NARROW, BLOCKED, gate_rectangle
from .viewport import overlaps
from .width_display import tint,low_ink
from .width_labels import LabelLayout

HEADERS = ['状态','固定 RDL','通道两侧','原可通','必须通过','现可通','原间距 / μm','现间距 / μm','保宽需 / μm','最小需 / μm','预期收窄 / μm','问题线路','位置 / μm']
INTRO = '按固定 RDL 原始线路穿越数比较各姿态的孔盘通道容量。勾选后默认低亮显示原始线路，矩形标记相邻孔盘通道。'


def low_risk(row):
    return bool((row.get('event_probability') or {}).get('low'))


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
            color.setAlpha(15 if low_risk(r) else 48)
            return color
        if role==DISPLAY:
            return (r['status']+(' · 低概率' if low_risk(r) else ''),r['plane'],' ↔ '.join(r['pair']),r['original'],r['required'],r['current'],
                    f"{r['nominal_gap_um']:.2f}",f"{r['gap_um']:.2f}",f"{r['keep_um']:.2f}",f"{r['minimum_um']:.2f}",f"{r['narrowing_um']:.3f}",
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
        self.label_sides={};self.text_sizes={}
        window.canvas.mpl_connect('motion_notify_event',self.hover)

    def draw_summary(self,summary,visible,mode):
        w=self.w;paths=[];colors=[];dashes=[]
        for (key,index),(severity,evidence) in summary['lines'].items():
            if key not in visible or mode=='hide':continue
            geometry=w.geometry_cache.layers[key]['geometry'][index]
            if not overlaps(geometry.bounds,w.view_bounds):continue
            paths.append(w.geometry_patch(geometry))
            uncertain=evidence=='undetermined'
            ink=np.asarray(to_rgb(COLORS[severity]))
            # Mix the dark local outline with white, even for proved risks.
            ink=.46*ink+.28 if not uncertain else .45*ink+.43
            colors.append((*ink,(.82 if not uncertain else .61)*(.62 if mode=='dim' else 1.)))
            dashes.append((0,(4,2.4)) if uncertain else 'solid')
        self.outlines.set_paths(paths);self.outlines.set_edgecolors(colors)
        self.outlines.set_linestyles(dashes or ['solid']);self.outlines.set_visible(bool(paths))
        for a in (self.boxes,self.edges,self.barriers,*self.inset.values(),*self.labels):a.set_visible(False)
        self.rows=[];self.tree=None

    def draw(self):
        w = self.w
        data = (w.current_frame or {}).get('width_analysis') if w.width_toggle.isChecked() else None
        if self.boxes is None or self.boxes.axes is not w.ax or self.boxes not in w.ax.collections:
            self.labels = []
            self.barriers = PatchCollection([],facecolor='#c3d2d8',edgecolor='#657d8b',linewidths=.6,zorder=4)
            self.boxes = PatchCollection([],linewidths=1.3,zorder=11.9)
            self.outlines = PatchCollection([],facecolors='none',linewidths=1.15,zorder=11.25)
            self.edges = PatchCollection([],linewidths=1.,zorder=11.1)
            self.inset = {s:PatchCollection([],linewidths=.4,hatch='///',zorder=11.6+i*.01) for i,s in enumerate((NARROW,BLOCKED))}
            for item in (self.barriers,self.boxes,self.outlines,self.edges,*self.inset.values()):
                w.ax.add_collection(item)
            self.stamp = self.table_stamp = None
        visible = frozenset(k for k,check in w.visible_boxes.items() if check.isChecked())
        flags = (id(data),w.width_sufficient_toggle.isChecked(),w.view_settings.show_low_width)
        modes=w.view_settings.width_display
        stamp = (flags,id(w._coupled_data),visible,tuple(modes.items()),w.view_settings.width_label_font_pt,w.view_settings.width_label_decimals,tuple(w.ax.bbox.bounds),w.figure.dpi,w.view_bounds,self.selected,id(w.current_frame),w.reconstruction_toggle.isChecked(),w.deformation_toggle.isChecked())
        if self.stamp == stamp and (getattr(w,'_in_animation',False) or self.table_stamp==flags):
            return
        self.stamp = stamp
        rules = (data or {}).get('parameters',{})
        if getattr(self,'row_filter',None)!=(flags,visible):
            self.row_filter=(flags,visible)
            self.table_rows=[r for r in (data or {}).get('rows',[]) if (flags[1] or r['status']!=ADEQUATE) and (flags[2] or not low_risk(r))]
            self.active_rows=[r for r in self.table_rows if visible.intersection(r['layers'])
                      and all(not ks or visible.intersection(ks) for ks in r['boundary_layers'])
                      and all(any(k in visible for k,_ in refs) for refs in r['boundary_refs'])
                      and (not r['refs'] or any(k in visible for k,_ in r['refs']))]
            if self.active_rows:
                points=np.asarray([r['point'] for r in self.active_rows])
                reach=np.array([max(r['gap_um']/2,rules['width_um']) for r in self.active_rows])+(.65*(rules['width_um']+rules['space_um']))
                self.row_boxes=np.column_stack((points-reach[:,None],points+reach[:,None]))
        rows=self.table_rows;active=self.active_rows
        summary=(w.current_frame or {}).get('width_summary') if w.width_toggle.isChecked() else None
        if summary is not None and (w.current_frame or {}).get('nominal_global'):
            if w.coupled_view():summary=w._coupled_data or {'lines':{}}
            self.draw_summary(summary,visible,modes['描边'])
            if self.table_stamp!=flags:self.table_stamp=flags;self.fill_table(rows,data)
            return
        self.outlines.set_linestyles('solid')
        if getattr(self,'geometry_data',None) is not data:
            self.geometry_data = data
            self.rectangles = {}
        def rect(r):
            key = id(r)
            if key not in self.rectangles:
                self.rectangles[key]=gate_rectangle(r,rules['width_um'],rules['space_um'])
            return self.rectangles[key]
        shown, rectangles = [], []
        if active:
            x0,y0,x1,y1=w.view_bounds;b=self.row_boxes
            for i in np.flatnonzero((b[:,2]>=x0)&(b[:,3]>=y0)&(b[:,0]<=x1)&(b[:,1]<=y1)):
                r=active[i];rectangle=rect(r)
                if overlaps(rectangle.bounds,w.view_bounds):
                    shown.append(r);rectangles.append(rectangle)
        objects = [o for o in (data or {}).get('retained',[]) if visible.intersection(o['layers'])
                   and modes[o['kind']]!='hide' and any(k in visible for k,_ in o['refs']) and overlaps(o['geometry'].bounds,w.view_bounds)]
        self.barriers.set_paths([w.geometry_patch(o['geometry']) for o in objects])
        palette={'运动过孔孔盘':('#aacbbd','#588574'),'固定过孔孔盘':('#aec2d7','#6c86a1'),'固定障碍':('#c7c8ce','#81808c')}
        self.barriers.set_facecolors([tint(palette[o['kind']][0],modes[o['kind']],.77) for o in objects])
        self.barriers.set_edgecolors([tint(palette[o['kind']][1],modes[o['kind']]) for o in objects])
        self.barriers.set_visible(bool(objects))
        self.boxes.set_paths([w.geometry_patch(g) for g in rectangles])
        colors = [COLORS[r['status']] for r in shown]
        self.boxes.set_facecolors([low_ink(tint(c,modes['截面'],.14),low_risk(r)) for c,r in zip(colors,shown)])
        self.boxes.set_edgecolors([low_ink(tint(c,modes['截面']),low_risk(r)) for c,r in zip(colors,shown)])
        self.boxes.set_visible(bool(shown) and modes['截面']!='hide')
        board_visible=any(k in visible for k,r in w.roles().items() if r=='封装边框')
        edges=[v for v in (data or {}).get('edge_violations',[]) if (flags[2] or not low_risk(v)) and v['layer'] in visible and board_visible and overlaps(v['geometry'].bounds,w.view_bounds)]
        self.edges.set_paths([w.geometry_patch(v['geometry']) for v in edges])
        self.edges.set_facecolor([low_ink(tint('#c44956',modes['边距'],.34),low_risk(v)) for v in edges])
        self.edges.set_edgecolor([low_ink(tint('#c44956',modes['边距']),low_risk(v)) for v in edges])
        self.edges.set_visible(bool(edges) and modes['边距']!='hide')
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
        def dark(color):return tuple(.64*v for v in to_rgb(color))
        significant={ref for r in active if r['status']!=ADEQUATE and not low_risk(r) for ref in r['refs']}
        danger={ref for r in active if r['status']==BLOCKED for ref in r['refs']
                if not low_risk(r) or ref not in significant}
        for k,i in refs:
            g = changed.get((k,i),w.geometry_cache.layers[k]['geometry'][i])
            if overlaps(g.bounds,w.view_bounds):
                paths.append(w.geometry_patch(g))
                inks.append(low_ink(tint(dark(COLORS[BLOCKED if (k,i) in danger else NARROW]),modes['描边'],.63),(k,i) not in significant))
        self.outlines.set_paths(paths)
        self.outlines.set_edgecolors(inks)
        self.outlines.set_visible(bool(paths) and modes['描边']!='hide')
        # Fill only the material belonging to this risk row, clipped to the
        # actual rectangle. The original copper outside it keeps the dark edge.
        fill_stamp=(id(data),id(frame),w.deformation_toggle.isChecked(),w.reconstruction_toggle.isChecked())
        if getattr(self,'fill_stamp',None)!=fill_stamp:
            self.fill_stamp=fill_stamp;self.clips={}
        fill_paths={s:[] for s in self.inset};fill_low={s:[] for s in self.inset}
        if modes['描边']!='hide' and modes['截面']!='hide':
            for r,rectangle in reversed(list(zip(shown,rectangles))):
                if r['status']==ADEQUATE:continue
                for ref in r['refs']:
                    k,i=ref
                    if k not in visible:continue
                    g=changed.get(ref,w.geometry_cache.layers[k]['geometry'][i])
                    token=(id(r),id(g))
                    if token not in self.clips:self.clips[token]=g.intersection(rectangle)
                    part=self.clips[token]
                    if part.area>0:
                        fill_paths[r['status']].append(w.geometry_patch(part));fill_low[r['status']].append(low_risk(r))
        ink_mode='dim' if 'dim' in (modes['描边'],modes['截面']) else 'show'
        for status,artist in self.inset.items():
            color=dark(COLORS[status])
            artist.set_paths(fill_paths[status])
            artist.set_facecolor([low_ink(tint(color,ink_mode,.32),q) for q in fill_low[status]])
            artist.set_edgecolor([low_ink(tint(color,ink_mode,.5),q) for q in fill_low[status]])
            artist.set_visible(bool(fill_paths[status]))
        self.rows = shown
        self.tree = STRtree(rectangles) if shown else None
        fontsize=w.view_settings.width_label_font_pt
        order=[] if modes['标注']=='hide' else sorted(range(len(shown)),key=lambda i:(self.identity(shown[i])!=self.selected,low_risk(shown[i])))
        layout=LabelLayout(w.ax,rectangles,fontsize,self.text_sizes,modes['截面']!='hide') if order else None
        texts=[self.label_text(r,w.view_settings.width_label_decimals) for r in shown] if order else []
        if layout:layout.prescreen(texts)
        count=0;next_sides={}
        for i in order:
            r=shown[i];text=texts[i]
            source=(r['plane'],r['pair'],tuple(map(tuple,r['nominal_witness_um'])))
            found=layout.place(i,text,self.label_sides.get(source))
            if found is None:continue
            side,tip,offset=found;next_sides[source]=side
            if count==len(self.labels):
                a=w.ax.annotate('',xy=(0,0),textcoords='offset points',xytext=(0,0),ha='center',va='center',
                                multialignment='left',zorder=12.6,bbox=dict(boxstyle='square,pad=0.15',facecolor='white',alpha=.94),
                                arrowprops=dict(arrowstyle='->',lw=.9,shrinkB=0))
                self.labels.append(a)
            a=self.labels[count];count+=1
            a.xy=tip;a.set_position(offset);a.set_text(text);a.set_fontsize(fontsize)
            low=low_risk(r)
            a.set_color(low_ink(tint(dark(COLORS[r['status']]),modes['标注']),low))
            a.arrow_patch.set_color(low_ink(tint(COLORS[r['status']],modes['标注']),low))
            a.get_bbox_patch().set_edgecolor(COLORS[r['status']]);a.get_bbox_patch().set_alpha(.33 if low else .94)
            a.set_visible(True)
        self.label_sides=next_sides
        for a in self.labels[count:]:a.set_visible(False)
        if not getattr(w,'_in_animation',False) and self.table_stamp!=flags:
            self.table_stamp = flags
            self.fill_table(rows,data)

    @staticmethod
    def label_text(r,decimals=2):
        amount=f"{r['narrowing_um']:.{decimals}f}"
        return f"原宽:{r['original']},必过:{r['required']},现宽:{r['current']}\n预期收窄:{amount}微米"

    @staticmethod
    def identity(r):
        return (r['plane'],r['pair'],tuple(r['point']))

    @staticmethod
    def description(r):
        note = '　按矩形内相邻刚性孔盘或障碍重新分段。' if r['split'] else ''
        event=r.get('event_probability')
        if event:note+=f"　此位置事件概率上界 {event['upper']:.3g}，良率门限 {event['threshold']:.3g}。"+('已确认低概率。' if event['low'] else '未证实为低概率。')
        elif r['status']!=ADEQUATE:note+='　该位置没有可用于淡显的概率证书。'
        if r.get('locked'):note+=f"　{r['locked']} 条落盘连接已按实体占用。"
        if r['unresolved']:
            note += '　存在未确定的芯片归属。'
        return (f"{' ↔ '.join(r['pair'])}　{r['status']}\n原可通 {r['original']} 根，必须通过 {r['required']} 根，"
                f"现可通 {r['current']} 根。预期收窄 {r['narrowing_um']:.3f} μm。原线宽 {', '.join(f'{v:.2f}' for v in r['widths_um'])} μm。"
                f"各空隙净宽 {', '.join(f'{v:.2f}' for v in r['slots_um']) or '0'} μm。{note}")

    def fill_table(self,rows,data):
        w = self.w
        w.width_rows = rows
        w.width_table.model().replace_rows(rows)
        if not data:
            w.width_summary.setText(INTRO)
        elif not data['examined']:
            w.width_summary.setText('原始布局中没有线路穿越可评估的相邻窄口。')
        else:
            q = data['counts']
            w.width_summary.setText(f"检查 {data['examined']} 个相邻截面。宽度足够 {q[ADEQUATE]} 个，"
                                    f"需局部收窄 {q[NARROW]} 个，宽度不足 {q[BLOCKED]} 个。表中 {len(rows)} 行。")

        if data and any(r.get('event_probability') for r in data['rows']):
            rare=sum(low_risk(r) for r in data['rows'])
            w.width_summary.setText(w.width_summary.text()+f" 已确认低概率通道 {rare} 个。")
        if data and data.get('edge_violations'):
            w.width_summary.setText(w.width_summary.text()+f" 封装边距不足 {len(data['edge_violations'])} 个图形。")
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
