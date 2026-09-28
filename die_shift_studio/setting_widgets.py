"""Compact value widgets with explicit editing; wheel filtering is application-wide."""
from .qt_compat import QtCore,QtGui,QtWidgets as W,Signal,run_dialog,ACCEPTED
from .pivots import PIVOT_CHOICES

class ColorButton(W.QPushButton):
    valueChanged=Signal()
    def __init__(self,parent=None):
        super().__init__(parent);self._color='#bc243e';self.setValue(self._color);self.clicked.connect(self.choose)
    def value(self):return self._color
    def setValue(self,color):
        self._color=color;self.setText(color);self.setStyleSheet('color:'+color+';font-weight:bold;')
    def choose(self):
        color=W.QColorDialog.getColor(QtGui.QColor(self._color),self,'标记颜色')
        if color.isValid():self.setValue(color.name());self.valueChanged.emit()

class PivotEditor(W.QWidget):
    valueChanged=Signal()
    def __init__(self,parent=None):
        super().__init__(parent);lay=W.QVBoxLayout(self);lay.setContentsMargins(0,0,0,0);lay.setSpacing(2)
        self.combo=W.QComboBox()
        for name,value in PIVOT_CHOICES:self.combo.addItem(name,value)
        lay.addWidget(self.combo);self.coordinates=W.QWidget();row=W.QHBoxLayout(self.coordinates);row.setContentsMargins(0,0,0,0);row.setSpacing(2);self.spins=[]
        for title in ('X','Y'):
            spin=W.QDoubleSpinBox();spin.setRange(-1e9,1e9);spin.setDecimals(3);spin.setKeyboardTracking(False);spin.setPrefix(title+' ');spin.setToolTip('相对名义芯片中心的图纸坐标 / μm');row.addWidget(spin);self.spins.append(spin);spin.valueChanged.connect(self.valueChanged)
        lay.addWidget(self.coordinates);self.coordinates.hide();self.combo.currentIndexChanged.connect(self.changed)
    def changed(self,*_):
        self.coordinates.setVisible(self.combo.currentData()=='custom');self.valueChanged.emit()
    def value(self):return [self.combo.currentData(),*[spin.value() for spin in self.spins]]
    def setValue(self,value):
        self.blockSignals(True);mode,x,y=value;self.combo.setCurrentIndex(max(0,self.combo.findData(mode)))
        for spin,v in zip(self.spins,(x,y)):spin.setValue(v)
        self.coordinates.setVisible(mode=='custom');self.blockSignals(False)

RANGE_COLORS=('#386cb0','#299985','#9255aa','#ca703e','#779035','#9e4770','#557fa9','#b39029','#755c99','#538a6e','#98624c','#777b86')
class RangeEditor(W.QPushButton):
    valueChanged=Signal()
    def __init__(self,parent=None):
        super().__init__(parent);self._values=[];self.setValue([]);self.clicked.connect(self.choose)
    def value(self):return [list(row) for row in self._values]
    def setValue(self,values):
        self._values=[list(row) for row in values];self.setText('附加范围：'+('、'.join(str(row[0])+'σ' for row in values) or '无'))
    def choose(self):
        dialog=W.QDialog(self);dialog.setWindowTitle('叠加目标范围与颜色');layout=W.QVBoxLayout(dialog);form=W.QFormLayout();layout.addLayout(form);widgets=[];selected=dict(self._values)
        for level in range(1,13):
            check=W.QCheckBox(str(level)+'σ');check.setChecked(level in selected);color=ColorButton();color.setValue(selected.get(level,RANGE_COLORS[level-1]));form.addRow(check,color);widgets.append((level,check,color))
        button=getattr(W.QDialogButtonBox,"StandardButton",W.QDialogButtonBox);buttons=W.QDialogButtonBox(button.Ok|button.Cancel);buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);layout.addWidget(buttons)
        if run_dialog(dialog)==ACCEPTED:
            self.setValue([[level,color.value()] for level,check,color in widgets if check.isChecked()]);self.valueChanged.emit()
