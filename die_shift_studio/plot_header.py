"""A single toolbar/coordinates row with a truly fixed horizontal yield centre."""
from .qt_compat import QtWidgets as W,QtCore,QtGui,Qt,ALIGN_CENTER,ALIGN_RIGHT,ALIGN_VCENTER


class PlotHeader(W.QWidget):
    def __init__(self,toolbar,label):
        super().__init__()
        self.toolbar=toolbar;self.label=label
        old=toolbar.locLabel
        action=next(a for a in toolbar.actions() if toolbar.widgetForAction(a) is old)
        toolbar.removeAction(action);action.deleteLater()
        self.coordinates=W.QLabel(self);self.coordinates.setAlignment(ALIGN_RIGHT|ALIGN_VCENTER)
        self.coordinates.setStyleSheet('font-size:11px')
        toolbar.locLabel=self.coordinates
        toolbar.setParent(self);label.setParent(self)
        toolbar.setStyleSheet('QToolButton {padding:3px; margin:0;}')
        self.label.setAlignment(ALIGN_CENTER)
        self.label.hide();self.label.installEventFilter(self);self.scale_interface(int)

    def scale_interface(self, pixels):
        self.pixels=pixels
        icon=pixels(18)
        self.toolbar.setIconSize(QtCore.QSize(icon,icon))
        self.setFixedHeight(max(pixels(32),self.toolbar.sizeHint().height()))
        self.label.set_font_sizes(pixels(12),pixels(8))
        self.layout_items()

    def eventFilter(self,obj,event):
        kinds=getattr(QtCore.QEvent,'Type',QtCore.QEvent)
        if obj is self.label and event.type() in (kinds.Show,kinds.Hide):self.layout_items()
        return super().eventFilter(obj,event)

    def resizeEvent(self,event):
        super().resizeEvent(event);self.layout_items()

    def layout_items(self):
        width=self.width();height=self.height()
        if self.label.isHidden():
            gutter=min(self.pixels(180),width//3)
            self.toolbar.setGeometry(0,0,width-gutter-4,height)
            self.coordinates.setGeometry(width-gutter,0,gutter,height);return
        ideal=self.toolbar.sizeHint().width()
        gutter=min(ideal,max(self.pixels(140),(width-self.pixels(480))//2),max(0,width//3))
        self.toolbar.setGeometry(0,0,gutter,height)
        self.coordinates.setGeometry(width-gutter,0,gutter,height)
        self.label.setGeometry(gutter+4,0,max(0,width-2*gutter-8),height)
        self.label.raise_()
