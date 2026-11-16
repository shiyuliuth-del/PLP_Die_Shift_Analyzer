"""All-DIE manual pose editor; coordinates and pivots share the engineering units."""
import math
import numpy as np
from .qt_compat import QtCore,QtWidgets as W
from .setting_widgets import PivotEditor


class ManualPreview(W.QDialog):
    def __init__(self,w):
        super().__init__(w);self.w=w;self.setWindowTitle('手动偏移');self.resize(800,340)
        self.rows=[];self.source=None;layout=W.QVBoxLayout(self)
        self.table=W.QTableWidget(0,5);self.table.setHorizontalHeaderLabels(['芯片','DX / μm','DY / μm','O / °','旋转中心'])
        self.table.verticalHeader().hide();layout.addWidget(self.table)
        line=W.QHBoxLayout();line.addStretch()
        self.preview_button=W.QPushButton('预览');self.restore_button=W.QPushButton('复原');close=W.QPushButton('关闭')
        for button in (self.preview_button,self.restore_button,close):line.addWidget(button)
        layout.addLayout(line);self.preview_button.clicked.connect(self.preview);self.restore_button.clicked.connect(self.restore);close.clicked.connect(self.hide)

    def rebuild(self):
        w=self.w;count=len(w.case['dies']) if w.case else 0
        if self.source is w.data and len(self.rows)==count:return
        self.source=w.data;self.rows=[];self.table.setRowCount(count)
        for i in range(count):
            self.table.setItem(i,0,W.QTableWidgetItem(f'DIE {i+1}'));fields=[]
            for column in range(1,4):
                spin=W.QDoubleSpinBox();spin.setRange(-1e9,1e9) if column<3 else spin.setRange(-360,360)
                spin.setDecimals(4);spin.setKeyboardTracking(False);self.table.setCellWidget(i,column,spin);fields.append(spin)
            pivot=PivotEditor();self.table.setCellWidget(i,4,pivot);pivot.valueChanged.connect(self.table.resizeRowsToContents)
            self.rows.append((*fields,pivot))
        for i,size in enumerate((60,115,115,100,290)):self.table.setColumnWidth(i,size)

    def sync(self,states=None):
        self.rebuild();w=self.w
        if not self.rows:return
        if states is None:states=w.shown_states
        if states is None:states=np.zeros((len(self.rows),3))
        p=w.params()
        for i,row in enumerate(self.rows):
            pose=states[i] if i<len(states) else np.zeros(3)
            for widget,value in zip(row[:3],(*pose[:2],math.degrees(pose[2]))):widget.setValue(float(value))
            local=p.for_die(i);row[3].setValue([local.rotation_center,local.rotation_x_um,local.rotation_y_um])
        self.table.resizeRowsToContents()

    def open_editor(self):
        if self.w.remote_locked:return
        try:
            from .core import build_case
            if self.w.data is None:raise ValueError('请先打开图纸并设置芯片边框')
            self.w.case=self.w.case or build_case(self.w.data,self.w.roles(),layer_order=self.w.ordered_layers())
            if not self.isVisible():self.sync()
            self.show();self.raise_();self.activateWindow()
        except Exception as exc:self.w.error(exc)

    def preview(self):
        w=self.w
        if w.remote_locked:return
        try:
            self.rebuild();p=w.params();count=len(self.rows)
            if not count:raise ValueError('未识别芯片，请设置芯片边框')
            centers=[row[3].value() for row in self.rows]
            old=[[p.for_die(i).rotation_center,p.for_die(i).rotation_x_um,p.for_die(i).rotation_y_um] for i in range(count)]
            if centers!=old:
                prefix='single' if count==1 else 'multi'
                w.per_die_values=[[getattr(p.for_die(i),prefix+'_bound_x_um'),getattr(p.for_die(i),prefix+'_bound_y_um'),p.for_die(i).bound_angle_deg] for i in range(count)]
                w.per_die_pivots=centers;w._per_die_initialized=True;w.per_die_table.setRowCount(0)
                w.per_die_toggle.setChecked(True);w.sync_per_die();w.invalidate()
            w.mouse_die.cancel_pending();w.mouse_die.exit()
            states=np.asarray([[row[0].value(),row[1].value(),math.radians(row[2].value())] for row in self.rows])
            w.manual_states=states;w.active_preview=None
            w.extreme_table.blockSignals(True);w.extreme_table.setCurrentCell(-1,0);w.extreme_table.blockSignals(False)
            w.request_preview()
        except Exception as exc:w.error(exc)

    def restore(self):
        self.w.restore_preview();self.sync(np.zeros((len(self.rows),3)))

    def state(self):
        return dict(values=[[r[0].value(),r[1].value(),r[2].value()] for r in self.rows],pivots=[r[3].value() for r in self.rows])

    def restore_fields(self,data):
        self.rebuild()
        if not isinstance(data,dict):return
        pivots=data.get('pivots',[])
        from .core import Parameters
        try:Parameters(die_pivots=pivots).validate(require_rdl=False)
        except (ValueError,TypeError):pivots=[]
        for row,pivot in zip(self.rows,pivots):row[3].setValue(pivot)
        for i,(row,values) in enumerate(zip(self.rows,data.get('values',()))):
            if isinstance(values,(list,tuple)) and len(values)==3 and all(isinstance(v,(int,float)) and math.isfinite(v) for v in values):
                for widget,value in zip(row[:3],values):widget.setValue(float(value))
        self.table.resizeRowsToContents()
