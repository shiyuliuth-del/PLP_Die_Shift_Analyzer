"""Row dragging without a row-number column and per-layer display editor."""
from .qt_compat import QtCore,QtGui,QtWidgets as W,Signal,Qt,ACCEPTED,run_dialog

class LayerDrag(QtCore.QObject):
    moved=Signal();finished=Signal();styleRequested=Signal(int)
    def __init__(self,table):
        super().__init__(table);self.table=table;self.press=None;self.dragging=False
        self.watch(table.viewport())
    def watch(self,widget):
        widget.setProperty('dsa_layer_drag_owner',id(self));widget.installEventFilter(self)
    def end_drag(self):
        started=self.dragging
        if self.press is not None:W.QApplication.instance().removeEventFilter(self)
        self.press=None;self.dragging=False
        if started:self.finished.emit()
    def eventFilter(self,obj,event):
        types=getattr(QtCore.QEvent,'Type',QtCore.QEvent);left=getattr(Qt,'MouseButton',Qt).LeftButton
        event_type=event.type()
        if self.press is not None and event_type in (types.WindowDeactivate,types.UngrabMouse):
            self.end_drag()
        if event_type not in (types.MouseButtonPress,types.MouseMove,types.MouseButtonRelease):return False
        if obj.property('dsa_layer_drag_owner')!=id(self):
            if self.press is not None and (event_type in (types.MouseButtonPress,types.MouseButtonRelease)
                                            or not event.buttons()&left):
                self.end_drag()
            return False
        if event_type==types.MouseMove and not event.buttons()&left:
            self.end_drag()
            return False
        pos=event.globalPosition().toPoint() if hasattr(event,'globalPosition') else event.globalPos()
        local=self.table.viewport().mapFromGlobal(pos)
        if event.type()==types.MouseButtonPress and event.button()==left:
            row=self.table.rowAt(local.y());col=self.table.columnAt(local.x())
            if row<0 or col not in (0,1,2):return False
            self.end_drag()
            self.press=(pos,row,col);W.QApplication.instance().installEventFilter(self)
            self.table.setCurrentCell(row,col);return True
        if self.press is None:return False
        if event.type()==types.MouseMove:
            if (pos-self.press[0]).manhattanLength()>=W.QApplication.startDragDistance():self.dragging=True
            if self.dragging:
                header=self.table.verticalHeader();row=self.table.rowAt(local.y())
                if row>=0:
                    source=header.visualIndex(self.press[1]);target=header.visualIndex(row)
                    if source!=target:header.moveSection(source,target);self.moved.emit()
                if local.y()<18:self.table.verticalScrollBar().setValue(self.table.verticalScrollBar().value()-1)
                if local.y()>self.table.viewport().height()-18:self.table.verticalScrollBar().setValue(self.table.verticalScrollBar().value()+1)
            return True
        if event.type()==types.MouseButtonRelease and event.button()==left:
            _,row,col=self.press;dragged=self.dragging
            self.end_drag()
            if not dragged and self.table.rowAt(local.y())==row:
                widget=self.table.cellWidget(row,col)
                if isinstance(widget,W.QCheckBox):widget.click()
                elif col==2:self.styleRequested.emit(row)
            return True
        return False

class LayerStyleDialog(W.QDialog):
    def __init__(self,title,style,parent=None):
        super().__init__(parent);self.setWindowTitle('图层显示 · '+title);self.style=dict(style);self.setMinimumWidth(330)
        form=W.QFormLayout(self);self.color=W.QPushButton();self.color.clicked.connect(self.choose_color);form.addRow('颜色',self.color)
        self.alpha=W.QDoubleSpinBox();self.alpha.setRange(0,100);self.alpha.setDecimals(1);self.alpha.setSuffix(' %');self.alpha.setValue((1-style['alpha'])*100);form.addRow('透明度（0为不透明）',self.alpha)
        self.fill=W.QCheckBox('实心填充');self.fill.setChecked(style['fill']);form.addRow('图形',self.fill)
        self.width=W.QDoubleSpinBox();self.width.setRange(.1,5);self.width.setSingleStep(.1);self.width.setValue(max(.1,style['width']));form.addRow('描边宽度 / pt',self.width)
        self.lines=W.QComboBox()
        for title,key in [('实线','-'),('虚线','--'),('点线',':')]:self.lines.addItem(title,key)
        self.lines.setCurrentIndex(max(0,self.lines.findData(style.get('linestyle','-'))));form.addRow('描边样式（轮廓模式）',self.lines)
        button=getattr(W.QDialogButtonBox,'StandardButton',W.QDialogButtonBox)
        buttons=W.QDialogButtonBox(button.Ok|button.Cancel);buttons.accepted.connect(self.accept);buttons.rejected.connect(self.reject);form.addRow(buttons)
        self.update_color()
    def update_color(self):self.color.setText(self.style['color']);self.color.setStyleSheet('background:'+self.style['color']+';color:#243447;')
    def choose_color(self):
        color=W.QColorDialog.getColor(QtGui.QColor(self.style['color']),self,'图层颜色')
        if color.isValid():self.style['color']=color.name();self.update_color()
    def value(self):return dict(color=self.style['color'],alpha=1-self.alpha.value()/100,fill=self.fill.isChecked(),width=self.width.value(),linestyle=self.lines.currentData())
