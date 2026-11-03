"""Display-only controls for GDS and derived channel elements."""
from .qt_compat import QtWidgets as W,Signal
from matplotlib.colors import to_rgba

from .settings import ELEMENTS,DEFAULTS

def low_ink(color,low):
    r,g,b,a=to_rgba(color)
    if not low:return (r,g,b,a)
    return (.58*r+.32,.58*g+.32,.58*b+.32,.30*a)


def tint(color,mode,alpha=1.):
    r,g,b,_=to_rgba(color)
    if mode=='dim':
        return (.62*r+.08,.62*g+.08,.62*b+.08,alpha*.34)
    return (r,g,b,0. if mode=='hide' else alpha)


class WidthDisplayEditor(W.QWidget):
    valueChanged=Signal()
    def __init__(self):
        super().__init__()
        layout=W.QFormLayout(self);layout.setContentsMargins(0,0,0,0);self.boxes={}
        for key,title in ELEMENTS.items():
            box=W.QComboBox()
            for text,value in [('显示','show'),('低亮','dim'),('隐藏','hide')]:box.addItem(text,value)
            layout.addRow(title,box);self.boxes[key]=box;box.currentIndexChanged.connect(lambda *_:self.valueChanged.emit())

    def value(self):
        return {k:b.currentData() for k,b in self.boxes.items()}

    def setValue(self,values):
        for k,v in values.items():
            b=self.boxes[k];b.blockSignals(True);b.setCurrentIndex(b.findData(v));b.blockSignals(False)
