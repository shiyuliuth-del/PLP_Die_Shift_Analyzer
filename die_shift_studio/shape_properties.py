"""Q properties of the selected source shapes, with transactional Job edits."""
from collections import defaultdict
import numpy as np
import shapely
from .qt_compat import QtCore,QtWidgets as W,Qt,DISPLAY,HORIZONTAL
from .gds_shapes import shape_nodes,outline,node_fingerprint,build_outline

EDIT=getattr(Qt,'ItemDataRole',Qt).EditRole


class Coordinates(QtCore.QAbstractTableModel):
    def __init__(self,editable,parent):
        super().__init__(parent)
        self.editable=editable
        self.rows=[]

    def reset(self,rows):
        self.beginResetModel();self.rows=rows;self.endResetModel()

    def rowCount(self,parent=QtCore.QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self,parent=QtCore.QModelIndex()):
        return 0 if parent.isValid() else 2

    def data(self,index,role=DISPLAY):
        if index.isValid() and role in (DISPLAY,EDIT):
            return str(self.rows[index.row()][index.column()])
        return None

    def flags(self,index):
        flags=super().flags(index)
        return flags | getattr(Qt,'ItemFlag',Qt).ItemIsEditable if self.editable else flags

    def setData(self,index,value,role=EDIT):
        if self.editable and index.isValid() and role==EDIT:
            self.rows[index.row()][index.column()]=str(value)
            self.dataChanged.emit(index,index,[DISPLAY,EDIT]);return True
        return False

    def headerData(self,section,orientation,role=DISPLAY):
        if role==DISPLAY:
            return ('X / μm','Y / μm')[section] if orientation==HORIZONTAL else str(section+1)
        return None


class ShapeProperties(W.QDialog):
    def __init__(self,window):
        super().__init__(window)
        self.setWindowTitle('选中图形属性 · Q')
        self.resize(645,550)
        self.w=window;self.data=window.data;self.drafts={};self.ref=None;self.changes=None
        layout=W.QVBoxLayout(self)
        refs=list(window.selection.selected)
        frame=window.current_frame or {}
        self.replacements={}
        for toggle,key in ((window.reconstruction_toggle,'reconstruction'),):
            if toggle.isChecked():
                for layer,items in frame.get(key,{}).get('replacements',{}).items():
                    for q in items:
                        linked=[(layer,i) for i in q['indices']]
                        if any(ref in refs for ref in linked):
                            refs.extend(linked);self.replacements.update({r:q['geometry'] for r in linked})
        self.groups=defaultdict(list);self.group_nodes=defaultdict(int);self.counts={}
        roles=window.roles()
        total=0
        for k,i in sorted(set(refs)):
            kind,xy,_=shape_nodes(self.data,k,i)
            self.groups[(roles[k],k,kind)].append((k,i));self.group_nodes[roles[k],k,kind]+=len(xy)
            total+=len(xy);self.counts[k,i]=len(xy)
        self.total_nodes=total;self.header=W.QLabel();layout.addWidget(self.header);self.update_node_count()
        form=W.QFormLayout();layout.addLayout(form)
        self.group=W.QComboBox();self.object=W.QComboBox()
        form.addRow('类别 / 图层',self.group);form.addRow('图形',self.object)
        self.summary=W.QLabel();layout.addWidget(self.summary)
        tabs=W.QTabWidget();layout.addWidget(tabs)
        page=W.QWidget();pv=W.QVBoxLayout(page);self.table=W.QTableView()
        self.model=Coordinates(True,self);self.table.setModel(self.model);pv.addWidget(self.table)
        controls=W.QHBoxLayout();pv.addLayout(controls)
        for name,func in [('插入节点',self.insert_node),('删除节点',self.remove_node),('恢复源坐标',self.restore)]:
            button=W.QPushButton(name);button.clicked.connect(func);controls.addWidget(button)
        tabs.addTab(page,'名义节点 · 可编辑')
        view=W.QWidget();vv=W.QVBoxLayout(view);self.ring=W.QComboBox();vv.addWidget(self.ring)
        vtable=W.QTableView();self.preview=Coordinates(False,self);vtable.setModel(self.preview);vv.addWidget(vtable)
        tabs.addTab(view,'当前预览轮廓 · 只读')
        self.ring.currentIndexChanged.connect(self.show_ring)
        self.message=W.QLabel();self.message.setWordWrap(True);layout.addWidget(self.message)
        buttons=W.QDialogButtonBox();opts=getattr(W.QDialogButtonBox,'StandardButton',W.QDialogButtonBox)
        buttons.setStandardButtons(opts.Ok|opts.Cancel);buttons.button(opts.Ok).setText('应用');buttons.button(opts.Cancel).setText('取消');buttons.accepted.connect(self.confirm);buttons.rejected.connect(self.reject);layout.addWidget(buttons)
        self.model.dataChanged.connect(lambda *_:self.message.clear())
        self.model.modelReset.connect(self.update_node_count)
        self.group.currentIndexChanged.connect(self.set_group);self.object.currentIndexChanged.connect(self.set_object)
        for names,items in self.groups.items():
            self.group.addItem(' · '.join(names)+f'（{len(items)} 个，{self.group_nodes[names]} 个节点）',names)
        if not self.groups:self.message.setText('请在结果图形中单击或框选 GDS 图形。')

    def update_node_count(self):
        if self.ref is not None:
            self.message.clear()
            count=len(self.model.rows);difference=count-self.counts[self.ref]
            self.counts[self.ref]=count;self.total_nodes+=difference
            keys=tuple(self.group.currentData());self.group_nodes[keys]+=difference
            self.group.setItemText(self.group.currentIndex(),' · '.join(keys)+f'（{len(self.groups[keys])} 个，{self.group_nodes[keys]} 个节点）')
            kind,_,source=shape_nodes(self.data,*self.ref)
            width=f" · PATH 宽度 {source['width']:g} μm" if source else ''
            self.summary.setText(f'{kind} · {count} 个节点{width} · 顶层 Cell 坐标')
        self.header.setText(f'已选择 {len(self.counts)} 个源图形 · {len(self.groups)} 类 · 共 {self.total_nodes} 个名义节点')

    def set_group(self,index):
        refs=self.groups[self.group.itemData(index)]
        self.object.blockSignals(True);self.object.clear()
        for ref in refs:self.object.addItem(f'{ref[0]}  #{ref[1]+1}',ref)
        self.object.blockSignals(False);self.set_object(0)

    def set_object(self,index):
        if index<0:return
        self.ref=tuple(self.object.itemData(index))
        _,xy,_=shape_nodes(self.data,*self.ref)
        rows=self.drafts.setdefault(self.ref,xy.tolist())
        self.model.reset(rows)
        k,i=self.ref;frame=self.w.current_frame or {}
        g=self.replacements.get(self.ref)
        if g is None:
            polys=frame.get('layers',{}).get(k,{}).get('polygons',self.data.layers[k])
            g=shapely.Polygon(polys[i])
        self.rings=[]
        self.ring.blockSignals(True);self.ring.clear()
        for j,part in enumerate(shapely.get_parts(g)):
            if part.geom_type!='Polygon':continue
            for n,r in enumerate((part.exterior,*part.interiors)):
                self.rings.append(outline(np.asarray(r.coords)));self.ring.addItem(f"部分 {j+1} · "+('外轮廓' if n==0 else f'内孔 {n}'))
        self.ring.blockSignals(False);self.show_ring(0)

    def show_ring(self,index):
        self.preview.reset(self.rings[index] if 0<=index<len(self.rings) else [])

    def insert_node(self):
        if self.ref is None:return
        rows=self.model.rows;i=self.table.currentIndex().row()
        i=i if i>=0 else len(rows)-1
        try:
            a=np.array(rows[i],float);b=np.array(rows[(i+1)%len(rows)],float)
            value=(a+b)/2 if len(rows)>1 else a+np.array([self.data.dbu_um,0.])
        except (ValueError,IndexError):
            value=np.array([0.,0.])
        rows.insert(i+1,value.tolist());self.model.reset(rows);self.table.setCurrentIndex(self.model.index(i+1,0))

    def remove_node(self):
        i=self.table.currentIndex().row()
        if i>=0:self.model.rows.pop(i);self.model.reset(self.model.rows)

    def restore(self):
        if self.ref is None:return
        source=getattr(self.data,'_source_snapshot',self.data)
        self.drafts[self.ref]=shape_nodes(source,*self.ref)[1].tolist();self.set_object(self.object.currentIndex())

    def confirm(self):
        base=getattr(self.data,'_source_snapshot',self.data)
        edits={(q['layer'],q['index']):q for q in self.data.node_edits}
        try:
            for ref,rows in self.drafts.items():
                kind,original,meta=shape_nodes(base,*ref)
                xy=np.asarray(rows,float)
                if np.array_equal(xy,original):
                    edits.pop(ref,None);continue
                build_outline(xy,meta,self.data.dbu_um)
                edits[ref]=dict(layer=ref[0],index=ref[1],original=node_fingerprint(kind,original),nodes=xy.tolist())
        except (ValueError,IndexError,TypeError,RuntimeError) as exc:
            self.message.setText(f'{ref[0]} #{ref[1]+1} · {exc}');return
        self.changes=[q for _,q in sorted(edits.items())];self.accept()
