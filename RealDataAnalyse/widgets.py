"""Small Qt widgets with bounded rendering and draggable table heights."""
from . import __version__
from die_shift_studio.qt_compat import QtCore, QtGui, QtWidgets as W


class HeightGrip(W.QWidget):
    def __init__(self, table, settings=None, key='table'):
        super().__init__()
        self.table, self.settings, self.key = table, settings, key
        self.drag = None
        self.setFixedHeight(10)
        self.setCursor(getattr(QtCore.Qt, 'CursorShape', QtCore.Qt).SizeVerCursor)
        self.setToolTip('拖动以调整表格高度')
        self.setAccessibleName('调整表格高度')

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setPen(QtGui.QColor('#9aa8b7'))
        center = self.rect().center()
        for offset in (-1, 2):
            painter.drawLine(center.x()-28, center.y()+offset, center.x()+28, center.y()+offset)

    @staticmethod
    def global_y(event):
        return event.globalPosition().y() if hasattr(event, 'globalPosition') else event.globalPos().y()

    def mousePressEvent(self, event):
        if event.button() == getattr(QtCore.Qt, 'MouseButton', QtCore.Qt).LeftButton:
            self.drag = (self.global_y(event), self.table.height())
            event.accept()

    def mouseMoveEvent(self, event):
        if self.drag:
            y, height = self.drag
            self.table.setFixedHeight(max(70, min(1600, round(height + self.global_y(event)-y))))
            event.accept()

    def mouseReleaseEvent(self, event):
        if self.drag:
            self.drag = None
            if self.settings:
                self.settings.setValue('height/'+self.key, self.table.height())
            event.accept()


def table_frame(table, height, settings=None, key='table'):
    frame = W.QWidget()
    layout = W.QVBoxLayout(frame); layout.setContentsMargins(0,0,0,0); layout.setSpacing(0)
    table.setMinimumHeight(0); table.setMaximumHeight(16777215)
    if settings:
        try: height = int(settings.value('height/'+key, height))
        except (ValueError, TypeError): pass
    table.setFixedHeight(max(70, min(1600, height)))
    table.verticalHeader().show()
    grip = HeightGrip(table, settings, key)
    frame.grip = grip
    layout.addWidget(table); layout.addWidget(grip)
    return frame


class ConversationColors(QtGui.QSyntaxHighlighter):
    def highlightBlock(self, text):
        prefix = text[:100]
        color = '#245b96' if 'RealDataAnalyse →' in prefix else '#226b43' if '→ RealDataAnalyse' in prefix else '#a03c32'
        self.setFormat(0, len(text), QtGui.QColor(color))
