"""Draw retained barriers, failed gate rectangles and affected source outlines."""
import numpy as np
import shapely
from shapely.ops import nearest_points
from matplotlib.colors import to_rgb
from matplotlib.collections import PatchCollection
from shapely.geometry import Point
from shapely.strtree import STRtree
from .qt_compat import QtWidgets as W, QtGui, QtCore, DISPLAY, TOOLTIP, BACKGROUND, HORIZONTAL
from .width_analysis import COLORS, ADEQUATE, NARROW, BLOCKED, gate_rectangle
from .viewport import overlaps
from .width_display import tint,low_ink
from .width_labels import LabelLayout
from .width_witness import pose_text
from .width_risk_probability import lookup,lookup_union,probability_text,probability_color

HEADERS = ['状态','固定 RDL','通道两侧','原可通','必须通过','现可通','原间距 / μm','现间距 / μm','保宽需 / μm','最小需 / μm','预期收窄 / μm','问题线路','位置 / μm']
IGNORED='已忽略'
IGNORED_COLOR='#95c8a5'

def ignored_row(row,ignored):
    return bool(row['refs']) and row['status']!=ADEQUATE and all(ref in ignored for ref in row['refs'])


INTRO = '按固定 RDL 原始线路穿越数比较各姿态的孔盘通道容量。勾选后默认低亮显示原始线路，矩形标记相邻孔盘通道。'


def low_risk(row):
    return bool((row.get('event_probability') or {}).get('low'))


class ChannelRows(QtCore.QAbstractTableModel):
    """Virtual cells keep a full-package 'show adequate' query responsive."""
    def __init__(self,parent):
        super().__init__(parent)
        self.rows = []
        self.ignored=frozenset()

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
            return WidthOverlay.description(r)+(self.probability_formatter(r) if hasattr(self,'probability_formatter') else '')
        if role==BACKGROUND:
            color = QtGui.QColor(IGNORED_COLOR if ignored_row(r,self.ignored) else COLORS[r['status']])
            color.setAlpha(15 if low_risk(r) else 48)
            return color
        if role==DISPLAY:
            return (r['status']+(' · 已忽略' if ignored_row(r,self.ignored) else '')+(' · 低概率' if low_risk(r) else ''),r['plane'],' ↔ '.join(r['pair']),r['original'],r['required'],r['current'],
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


def visible_keepout_walls(row,visible,cache,dbu):
    """A merged computational wall must not reveal a hidden source layer."""
    ends=row.get('witness_um',row.get('nominal_witness_um'))
    if ends is None:return True
    for side,kind in enumerate(row['ends']):
        if kind!='禁布区':continue
        refs=row['boundary_refs'][side]
        if all(k in visible for k,_ in refs):continue
        point=Point(ends[side])
        if not any(k in visible and cache.layers[k]['geometry'][i].distance(point)<=max(1e-9,dbu*1e-5)
                   for k,i in refs):return False
    return True


def visible_keepout_intrusion(row,visible,cache):
    if row['kind']!='禁布区侵入':return row
    refs=row['boundary_refs']
    if all(k in visible for k,_ in refs):return row
    pieces=[cache.layers[k]['geometry'][i] for k,i in refs if k in visible]
    if not pieces:return None
    geometry=row['geometry'].intersection(shapely.union_all(pieces))
    return dict(row,geometry=geometry) if geometry.area>0 else None


class WidthOverlay:
    def __init__(self, window):
        self.w = window
        self.barriers = self.boxes = self.outlines = None
        self.labels = []
        self.rows = []
        self.tree = None
        self.selected = None
        self.stamp = self.table_stamp = self.summary_stamp = None
        self.label_sides={};self.text_sizes={}
        window.canvas.mpl_connect('motion_notify_event',self.hover)
        window.width_table.model().probability_formatter=self.probability_line

    def shown_summary(self):
        w=self.w;frame=w.current_frame or {}
        if not w.width_toggle.isChecked() or not frame.get('nominal_global'):return None
        base=frame.get('width_summary')
        if base is not None and w.coupled_view():return w._coupled_data or {'lines':{}}
        return base

    def reset_summary(self):
        self.label_source=None;self.pose_texts={}
        self.summary_stamp=None

    def set_label(self,n,text,tip,offset,fontsize,status,mode,low=False,summary=False):
        from .canvas_callouts import put
        color=COLORS[status];dark=np.asarray(to_rgb(color))*.64
        put(self.w,self.labels,n,text,tip,offset,fontsize,color,low_ink(tint(dark,mode),low),
            low_ink(tint(color,mode),low),.33 if low else .94,15 if summary else 12.6)

    def line_probability(self,ref):
        return lookup(self.w.width_yield.bank,'lines',ref)

    def problem_probability(self,row):
        bank=self.w.width_yield.bank
        if not hasattr(self,'probability_unions') or self.probability_bank is not bank:
            self.probability_bank=bank;self.probability_unions={}
        keys=row.get('probability_keys')
        if keys is None:
            if 'indices' in row:
                from .width_event_ids import edge_keys
                keys=edge_keys(row)
            else:keys=(row.get('probability_key'),)
        keys=tuple(keys)
        if keys not in self.probability_unions:self.probability_unions[keys]=lookup_union(bank,keys)
        return self.probability_unions[keys]

    def probability_line(self,row):
        if not self.w.view_settings.width_show_probability:return ''
        value=self.problem_probability(row)
        frame=self.w.current_frame or {};changed=set()
        for feature in ('deformation','reconstruction'):
            for key,items in frame.get(feature,{}).get('replacements',{}).items():
                changed.update((key,i) for q in items for i in q['indices'])
        keys=row.get('probability_keys',())
        if 'indices' in row:
            from .width_event_ids import edge_keys
            keys=edge_keys(row)
        reference=any(k[0] in ('edge','keepout') and (k[1],k[2]) in changed for k in keys)
        return ('\n原图问题概率：' if reference else '\n问题概率：')+probability_text(value,self.w.view_settings.width_yield_decimals)

    def summary_entry(self,ref,item,summary):
        places=self.w.view_settings.width_label_decimals
        if item['kind']=='channel':
            body=self.label_text(item,places)
        elif item['kind']=='keepout':
            body=f"禁布区侵入 {item['intrusion_area_um2']:.{places}f} μm²\n允许贴边，不允许进入"
        else:
            body=(f"封装边距 {item['gap_um']:.{places}f} μm · 要求 {item['required_um']:.{places}f} μm"
                  f"\n不足 {item['shortfall_um']:.{places}f} μm")
        pose=item['index']
        if pose not in self.pose_texts:self.pose_texts[pose]=pose_text(summary['grid_key'],pose)
        status=summary['lines'][ref][0]
        return status,f"{ref[0]} #{ref[1]+1} · {status}\n{body}"+self.probability_line(item)+'\n'+self.pose_texts[pose]

    def label_anchor(self,geometry,target=None):
        w=self.w
        view=shapely.box(min(w.ax.get_xlim()),min(w.ax.get_ylim()),max(w.ax.get_xlim()),max(w.ax.get_ylim()))
        outside=not view.intersects(geometry)
        if outside:anchor=nearest_points(view,geometry)[0]
        else:
            copper=geometry if view.covers(geometry) else geometry.intersection(view)
            anchor=nearest_points(target or copper.representative_point(),copper)[1]
        xy=w.ax.transData.transform(anchor.coords[0])
        xy=np.clip(xy,w.ax.bbox.min+1,w.ax.bbox.max-1)
        inv=w.ax.transData.inverted();scale=w.canvas.get_renderer().points_to_pixels(2.5)
        return shapely.box(*inv.transform(xy-scale),*inv.transform(xy+scale)),tuple(inv.transform(xy)),outside

    def retarget(self,position,tip):
        side,old,offset,font=position;w=self.w;scale=w.canvas.get_renderer().points_to_pixels(1)
        center=w.ax.transData.transform(old)+np.asarray(offset)*scale
        return side,tip,tuple((center-w.ax.transData.transform(tip))/scale),font

    def summary_labels(self,summary,selected,visible,modes):
        w=self.w;items=[];boxes=[];anchors=[];outside=[]
        if getattr(self,'label_source',None) is not summary:
            self.label_source=summary;self.pose_texts={}
        size=w.view_settings.width_label_font_pt
        if modes['描边']!='hide' and modes['标注']!='hide':
            for key,index in selected:
                ref=(key,index);item=summary.get('witnesses',{}).get(ref)
                if ref in w.ignored_width_lines or key not in visible or item is None:continue
                geometry=w.geometry_cache.layers[key]['geometry'][index]
                target=(shapely.LineString(item['nominal_ends']).centroid if item['kind']=='channel' else Point(item['nominal_point']))
                rectangle,anchor,out=self.label_anchor(geometry,target)
                severity,message=self.summary_entry(ref,item,summary)
                if out:message+='\n目标线路在当前视野外'
                items.append((severity,message));boxes.append(rectangle);anchors.append(anchor);outside.append(out)
        count=0
        if items:
            layout=LabelLayout(w.ax,boxes,size,self.text_sizes)
            if w.legend is not None and w.legend.get_visible():
                box=w.legend.get_window_extent(w.canvas.get_renderer())
                layout.labels.append((np.array([box.x0,box.y0])/layout.scale,np.array([box.x1,box.y1])/layout.scale))
            for i,(severity,message) in enumerate(items):
                near=layout.place_selected(i,message)
                position=(*near,size) if near else layout.place_pinned(i,message)
                if position is None:continue
                if outside[i]:position=self.retarget(position,anchors[i])
                _,tip,offset,font=position
                self.set_label(count,message,tip,offset,font,severity,modes['标注'],summary=True);count+=1
        for label in self.labels[count:]:label.set_visible(False)

    def draw_summary(self,summary,visible,modes,selected):
        w=self.w;mode=modes['描边']
        stamp=(id(summary),visible,mode,w.view_bounds,w.ignored_width_lines,id(w.width_yield.bank))
        if stamp!=self.summary_stamp:
            paths=[];colors=[];faces=[];dashes=[]
            for (key,index),(severity,evidence) in summary['lines'].items():
                if key not in visible or mode=='hide':continue
                geometry=w.geometry_cache.layers[key]['geometry'][index]
                if not overlaps(geometry.bounds,w.view_bounds):continue
                paths.append(w.geometry_patch(geometry))
                uncertain=evidence=='undetermined';ignored=(key,index) in w.ignored_width_lines
                ink=np.asarray(to_rgb(IGNORED_COLOR if ignored else COLORS[severity]))
                edge=ink if ignored else (.45*ink+.43 if uncertain else .46*ink+.28)
                factor=.62 if mode=='dim' else 1.
                colors.append((*edge,(.61 if uncertain else .82)*factor))
                faces.append(tint(probability_color(ink,self.line_probability((key,index))),mode,.20))
                dashes.append((0,(4,2.4)) if uncertain else 'solid')
            self.outlines.set_paths(paths);self.outlines.set_edgecolors(colors);self.outlines.set_facecolors(faces)
            self.outlines.set_linestyles(dashes or ['solid']);self.outlines.set_visible(bool(paths));self.summary_stamp=stamp
            self.outlines._static_preview=('width-zero',stamp)
        for a in (self.boxes,self.edges,self.barriers,*self.inset.values()):a.set_visible(False)
        self.rows=[];self.tree=None;self.summary_labels(summary,selected,visible,modes)

    def draw(self):
        w = self.w
        data = (w.current_frame or {}).get('width_analysis') if w.width_toggle.isChecked() else None
        if self.boxes is None or self.boxes.axes is not w.ax or self.boxes not in w.ax.collections:
            self.labels = []
            self.barriers = PatchCollection([],facecolor='#c3d2d8',edgecolor='#657d8b',linewidths=.6,zorder=4)
            self.boxes = PatchCollection([],linewidths=1.3,zorder=11.9)
            self.outlines = PatchCollection([],facecolors='none',linewidths=1.15,zorder=11.25)
            self.edges = PatchCollection([],linewidths=1.,zorder=11.1)
            self.inset = {s:PatchCollection([],linewidths=.4,hatch='///',zorder=11.6+i*.01) for i,s in enumerate((NARROW,BLOCKED,IGNORED))}
            for item in (self.barriers,self.boxes,self.outlines,self.edges,*self.inset.values()):
                w.ax.add_collection(item)
            self.stamp = self.table_stamp = self.summary_stamp = None
        visible = frozenset(k for k,check in w.visible_boxes.items() if check.isChecked())
        flags = (id(data),w.width_sufficient_toggle.isChecked(),w.view_settings.show_low_width,w.ignored_width_lines)
        w.width_table.model().ignored=w.ignored_width_lines
        modes=w.view_settings.width_display
        nominal=w.width_toggle.isChecked() and (w.current_frame or {}).get('nominal_global')
        selected=tuple(w.selection.selected)
        stamp = (flags,selected,id(w.width_yield.bank),w.view_settings.width_show_probability,w.view_settings.width_yield_decimals,tuple(w.ax.get_xlim()),tuple(w.ax.get_ylim()),id(w._coupled_data),visible,tuple(modes.items()),w.view_settings.width_label_font_pt,w.view_settings.width_label_decimals,tuple(w.ax.bbox.bounds),w.figure.dpi,w.view_bounds,self.selected,id(w.current_frame),w.reconstruction_toggle.isChecked(),False)
        if self.stamp == stamp and (getattr(w,'_in_animation',False) or self.table_stamp==flags):
            return
        self.stamp = stamp
        if data is None:
            if self.table_stamp!=flags:
                for a in (self.barriers,self.boxes,self.outlines,self.edges,*self.inset.values(),*self.labels):a.set_visible(False)
                self.outlines.set_facecolors('none');self.summary_stamp=None;self.rows=[];self.tree=None
                self.table_stamp=flags;self.fill_table([],None)
            return
        rules = (data or {}).get('parameters',{})
        if getattr(self,'row_filter',None)!=(flags,visible):
            self.row_filter=(flags,visible)
            self.table_rows=[r for r in (data or {}).get('rows',[]) if (flags[1] or r['status']!=ADEQUATE) and (flags[2] or not low_risk(r))]
            self.active_rows=[r for r in self.table_rows if visible.intersection(r['layers'])
                      and all(not ks or visible.intersection(ks) for ks in r['boundary_layers'])
                      and all(any(k in visible for k,_ in refs) for refs in r['boundary_refs'])
                      and (not r['refs'] or any(k in visible for k,_ in r['refs']))
                      and visible_keepout_walls(r,visible,w.geometry_cache,w.data.dbu_um)]
            if self.active_rows:
                points=np.asarray([r['point'] for r in self.active_rows])
                reach=np.array([max(r['gap_um']/2,rules['width_um']) for r in self.active_rows])+(.65*(rules['width_um']+rules['space_um']))
                self.row_boxes=np.column_stack((points-reach[:,None],points+reach[:,None]))
        rows=self.table_rows;active=self.active_rows
        summary=self.shown_summary()
        if summary is not None:
            self.draw_summary(summary,visible,modes,selected)
            if self.table_stamp!=flags:self.table_stamp=flags;self.fill_table(rows,data)
            return
        self.summary_stamp=None;self.outlines.set_facecolors('none');self.outlines.set_linestyles('solid')
        if hasattr(self.outlines,'_static_preview'):del self.outlines._static_preview
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
        # Keepouts already have exact, per-source GDS artists. Do not redraw a
        # merged computational union on top of hidden contributing layers.
        objects = [o for o in (data or {}).get('retained',[]) if o['kind']!='禁布区' and visible.intersection(o['layers'])
                   and modes[o['kind']]!='hide' and any(k in visible for k,_ in o['refs']) and overlaps(o['geometry'].bounds,w.view_bounds)]
        self.barriers.set_paths([w.geometry_patch(o['geometry']) for o in objects])
        palette={'运动过孔孔盘':('#aacbbd','#588574'),'固定过孔孔盘':('#aec2d7','#6c86a1'),'固定障碍':('#c7c8ce','#81808c')}
        self.barriers.set_facecolors([tint(palette[o['kind']][0],modes[o['kind']],.77) for o in objects])
        self.barriers.set_edgecolors([tint(palette[o['kind']][1],modes[o['kind']]) for o in objects])
        self.barriers.set_visible(bool(objects))
        self.boxes.set_paths([w.geometry_patch(g) for g in rectangles])
        colors = [IGNORED_COLOR if ignored_row(r,w.ignored_width_lines) else COLORS[r['status']] for r in shown]
        self.boxes.set_facecolors([low_ink(tint(c,modes['截面'],.20),low_risk(r)) for c,r in zip(colors,shown)])
        self.boxes.set_edgecolors([low_ink(tint(c,modes['截面']),low_risk(r)) for c,r in zip(colors,shown)])
        self.boxes.set_visible(bool(shown) and modes['截面']!='hide')
        board_visible=any(k in visible for k,r in w.roles().items() if r=='封装边框')
        edges=[v for v in (data or {}).get('edge_violations',[]) if (flags[2] or not low_risk(v)) and v['layer'] in visible
               and (bool(visible.intersection(v['boundary_layers'])) if v['kind']=='禁布区侵入' else board_visible)
               and overlaps(v['geometry'].bounds,w.view_bounds)]
        edges=[q for v in edges if (q:=visible_keepout_intrusion(v,visible,w.geometry_cache)) is not None]
        self.edges.set_paths([w.geometry_patch(v['geometry']) for v in edges])
        edge_colors=[IGNORED_COLOR if all((v['layer'],i) in w.ignored_width_lines for i in v['indices']) else COLORS[BLOCKED] for v in edges]
        self.edges.set_facecolor([tint(probability_color(c,self.problem_probability(v)),modes['边距'],.40) for c,v in zip(edge_colors,edges)])
        self.edges.set_edgecolor([low_ink(tint(c,modes['边距']),low_risk(v)) for c,v in zip(edge_colors,edges)])
        self.edges.set_visible(bool(edges) and modes['边距']!='hide')
        # Use the displayed replacement geometry when a source wire was deformed.
        changed = {}
        frame = w.current_frame or {}
        for toggle,feature in ((w.reconstruction_toggle,'reconstruction'),):
            if toggle.isChecked():
                for k,items in frame.get(feature,{}).get('replacements',{}).items():
                    for q in items:
                        changed.update({(k,i):q['geometry'] for i in q['indices']})
        refs = dict.fromkeys(ref for r in active if r['status']!=ADEQUATE for ref in r['refs'] if ref[0] in visible)
        paths, inks, faces = [], [], []
        def dark(color):return tuple(.64*v for v in to_rgb(color))
        significant={ref for r in active if r['status']!=ADEQUATE and not low_risk(r) for ref in r['refs']}
        danger={ref for r in active if r['status']==BLOCKED for ref in r['refs']
                if not low_risk(r) or ref not in significant}
        for k,i in refs:
            g = changed.get((k,i),w.geometry_cache.layers[k]['geometry'][i])
            if overlaps(g.bounds,w.view_bounds):
                paths.append(w.geometry_patch(g))
                color=IGNORED_COLOR if (k,i) in w.ignored_width_lines else dark(COLORS[BLOCKED if (k,i) in danger else NARROW])
                inks.append(low_ink(tint(color,modes['描边'],.8),(k,i) not in significant))
                base=IGNORED_COLOR if (k,i) in w.ignored_width_lines else COLORS[BLOCKED if (k,i) in danger else NARROW]
                faces.append(tint(probability_color(base,self.line_probability((k,i))),modes['描边'],.20))
        self.outlines.set_paths(paths)
        self.outlines.set_edgecolors(inks);self.outlines.set_facecolors(faces)
        self.outlines.set_visible(bool(paths) and modes['描边']!='hide')
        # Fill only the material belonging to this risk row, clipped to the
        # actual rectangle. The original copper outside it keeps the dark edge.
        fill_stamp=(id(data),id(frame),False,w.reconstruction_toggle.isChecked())
        if getattr(self,'fill_stamp',None)!=fill_stamp:
            self.fill_stamp=fill_stamp;self.clips={}
        fill_paths={s:[] for s in self.inset};fill_low={s:[] for s in self.inset};fill_prob={s:[] for s in self.inset}
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
                        style=IGNORED if ref in w.ignored_width_lines else r['status']
                        fill_paths[style].append(w.geometry_patch(part));fill_low[style].append(low_risk(r));fill_prob[style].append(self.line_probability(ref))
        ink_mode='dim' if 'dim' in (modes['描边'],modes['截面']) else 'show'
        for status,artist in self.inset.items():
            color=IGNORED_COLOR if status==IGNORED else dark(COLORS[status])
            artist.set_paths(fill_paths[status])
            artist.set_facecolor([tint(probability_color(color,q),ink_mode,.40) for q in fill_prob[status]])
            artist.set_edgecolor([low_ink(tint(color,ink_mode,.5),q) for q in fill_low[status]])
            artist.set_visible(bool(fill_paths[status]))
        self.rows = shown
        self.tree = STRtree(rectangles) if shown else None
        fontsize=w.view_settings.width_label_font_pt
        label_rows=list(shown);label_rects=list(rectangles);anchors={}
        shown_ids={id(r) for r in shown};selected_refs=set(w.selection.selected)
        for row in active:
            wanted=self.identity(row)==self.selected or bool(selected_refs.intersection(row['refs']))
            if not wanted or id(row) in shown_ids or ignored_row(row,w.ignored_width_lines):continue
            geos=[]
            for ref in row['refs']:
                key,index=ref
                if key not in visible:continue
                geo=changed.get(ref)
                if geo is None:
                    arrays=(w.current_frame or {}).get('layers',{}).get(key,{}).get('polygons')
                    geo=shapely.Polygon(arrays[index]) if arrays is not None else w.geometry_cache.layers[key]['geometry'][index]
                if not geo.is_empty:geos.append(geo)
            if not geos:continue
            rect,tip,out=self.label_anchor(shapely.union_all(geos),Point(row['point']))
            if not out:continue
            anchors[len(label_rows)]=tip;label_rows.append(row);label_rects.append(rect)
        order=[] if modes['标注']=='hide' else sorted((i for i,r in enumerate(label_rows) if not ignored_row(r,w.ignored_width_lines)),
                                key=lambda i:(self.identity(label_rows[i])!=self.selected,low_risk(label_rows[i])))
        layout=LabelLayout(w.ax,label_rects,fontsize,self.text_sizes,modes['截面']!='hide') if order else None
        texts=[self.label_text(r,w.view_settings.width_label_decimals)+self.probability_line(r)+('\n目标线路在当前视野外' if i in anchors else '') for i,r in enumerate(label_rows)] if order else []
        if layout:layout.prescreen(texts)
        count=0;next_sides={}
        for i in order:
            r=label_rows[i];text=texts[i]
            source=(r['plane'],r['pair'],tuple(map(tuple,r['nominal_witness_um'])))
            found=layout.place(i,text,self.label_sides.get(source))
            font=fontsize
            wanted=self.identity(r)==self.selected or bool(selected_refs.intersection(r['refs']))
            if found is None and wanted:
                near=layout.place_selected(i,text)
                fitted=(*near,fontsize) if near else layout.place_pinned(i,text)
                if fitted:found=fitted[:3];font=fitted[3]
            if found is None:continue
            if i in anchors:found=self.retarget((*found,font),anchors[i])[:3]
            side,tip,offset=found;next_sides[source]=side
            self.set_label(count,text,tip,offset,font,r['status'],modes['标注'],low_risk(r));count+=1
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
            w.width_summary.setText(w.width_summary.text()+f" 封装边缘或禁布区违规 {len(data['edge_violations'])} 个图形。")
    def hover(self,event):
        w = self.w
        if self.tree is None or event.inaxes is not w.ax or event.xdata is None:
            if w.canvas.toolTip():
                w.canvas.setToolTip('')
            return
        hits = self.tree.query(Point(event.xdata,event.ydata),predicate='intersects')
        text = self.description(self.rows[int(hits[0])])+self.probability_line(self.rows[int(hits[0])]) if len(hits) and not ignored_row(self.rows[int(hits[0])],w.ignored_width_lines) else ''
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
