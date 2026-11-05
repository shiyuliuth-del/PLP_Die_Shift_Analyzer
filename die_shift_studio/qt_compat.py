import os
try:
    from PyQt6 import QtCore,QtGui,QtWidgets
    os.environ['QT_API']='pyqt6'
    BINDING='PyQt6'
except ImportError:
    from PyQt5 import QtCore,QtGui,QtWidgets
    os.environ['QT_API']='pyqt5'
    BINDING='PyQt5'
Signal=QtCore.pyqtSignal
Qt=QtCore.Qt
HORIZONTAL=getattr(Qt,'Orientation',Qt).Horizontal
VERTICAL=getattr(Qt,'Orientation',Qt).Vertical
DISPLAY=getattr(Qt,'ItemDataRole',Qt).DisplayRole
BACKGROUND=getattr(Qt,'ItemDataRole',Qt).BackgroundRole
TOOLTIP=getattr(Qt,'ItemDataRole',Qt).ToolTipRole
ALIGNMENT=getattr(Qt,'ItemDataRole',Qt).TextAlignmentRole
ALIGN_RIGHT=getattr(Qt,'AlignmentFlag',Qt).AlignRight
ALIGN_CENTER=getattr(Qt,'AlignmentFlag',Qt).AlignCenter
ALIGN_VCENTER=getattr(Qt,'AlignmentFlag',Qt).AlignVCenter
ASCENDING=getattr(Qt,'SortOrder',Qt).AscendingOrder
ACCEPTED=getattr(QtWidgets.QDialog,'DialogCode',QtWidgets.QDialog).Accepted

def run_dialog(dialog):
    return dialog.exec() if hasattr(dialog,'exec') else dialog.exec_()
