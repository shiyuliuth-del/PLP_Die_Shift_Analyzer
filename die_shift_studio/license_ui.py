"""Activation and permanent-license status, sharing the application's Qt theme."""
from . import __version__
from .license_core import LicenseError, local_authorization
from .qt_compat import QtCore, QtWidgets as W, Signal, run_dialog, ACCEPTED
from .style import STYLE


class LicenseQuery(QtCore.QThread):
    resolved = Signal(object, str)

    def __init__(self, parent, authorization=None, path=None):
        super().__init__(parent)
        self.authorization=authorization
        self.path=path

    def run(self):
        state=self.authorization
        try:
            if state is None:
                state=local_authorization()
            if self.path is None:
                state.check()
            else:
                state.activate(self.path)
        except (LicenseError, OSError) as exc:
            self.resolved.emit(state, str(exc))
        else:
            self.resolved.emit(state, '')


class LicenseDialog(W.QDialog):
    def __init__(self, parent=None, authorization=None):
        super().__init__(parent)
        self.authorization = authorization
        self.selected = None
        self.query = None
        self.setWindowTitle(f'Die Shift Analyze {__version__} · 授权管理')
        self.setStyleSheet(STYLE)
        self.setMinimumWidth(560)
        layout = W.QFormLayout(self)
        self.status = W.QLabel('正在读取本机硬件信息…')
        self.status.setWordWrap(True)
        self.machine = W.QLineEdit(readOnly=True)
        layout.addRow('Machine ID', self.machine)
        layout.addRow(self.status)
        row = W.QHBoxLayout()
        self.copy = W.QPushButton('复制 Machine ID')
        self.import_btn = W.QPushButton('导入许可证')
        self.apply_btn = W.QPushButton('激活')
        self.exit_btn = W.QPushButton('关闭' if parent else '退出')
        for button in (self.copy,self.import_btn,self.apply_btn,self.exit_btn):
            row.addWidget(button)
        layout.addRow(row)
        self.copy.clicked.connect(lambda:W.QApplication.clipboard().setText(self.machine.text()))
        self.import_btn.clicked.connect(self.pick)
        self.apply_btn.clicked.connect(self.apply)
        self.exit_btn.clicked.connect(self.reject)
        self.apply_btn.setEnabled(False)
        self.copy.setEnabled(False)
        self.import_btn.setEnabled(False)
        if authorization is None:
            self.start_query()
        else:
            self.show_status(authorization, '' if authorization.payload else '尚未导入许可证。')

    def start_query(self, path=None):
        self.exit_btn.setEnabled(False)
        self.import_btn.setEnabled(False)
        self.apply_btn.setEnabled(False)
        self.query=LicenseQuery(self,self.authorization,path)
        self.query.resolved.connect(self.query_finished)
        self.query.start()

    def query_finished(self, authorization, error):
        query=self.query
        query.wait()
        imported=query.path is not None
        self.query=None
        query.deleteLater()
        self.exit_btn.setEnabled(True)
        self.show_status(authorization,error)
        if not error and (imported or self.parent() is None):
            self.accept()

    def show_status(self, authorization, error):
        self.authorization = authorization
        if authorization is None:
            self.status.setText(error)
            return
        self.machine.setText(authorization.machine_id)
        self.machine.setToolTip(f'Machine ID V{authorization.bindings.version}')
        self.copy.setEnabled(bool(authorization.machine_id))
        self.import_btn.setEnabled(True)
        self.apply_btn.setEnabled(self.selected is not None)
        if error:
            message = f'{error} 请将 Machine ID 发给管理员。' if authorization.machine_id else '\n'.join(dict.fromkeys(filter(None,(error,authorization.binding_error))))
            self.status.setText(message)
        else:
            payload = authorization.payload
            self.status.setText(f"永久授权 · {payload['user_name']} · {payload['employee_id']}\n授权编号 {payload['license_id']}")

    def pick(self):
        path, _ = W.QFileDialog.getOpenFileName(self,'导入许可证','','许可证 (*.lic)')
        if path:
            self.selected = path
            self.apply_btn.setEnabled(True)
            self.apply()

    def apply(self):
        if not self.selected or self.authorization is None or self.query is not None:
            return
        self.status.setText('正在验证许可证…')
        self.start_query(self.selected)

    def reject(self):
        if self.query is None:
            super().reject()


def require_activation():
    app = W.QApplication.instance() or W.QApplication([])
    dialog = LicenseDialog()
    result = run_dialog(dialog)
    if dialog.query is not None:
        dialog.query.wait()
    return (app,dialog.authorization) if result==ACCEPTED else (app,None)
